#!/usr/bin/env bash
# Pull the closed log files of past days from the MPP master into the receiving
# directories and place the completeness marker of each day that arrived whole.
#
# Runs on the baseline server. Read-only towards the source: it lists and copies,
# nothing else. One connection at a time. Key login only: without it the script
# fails at once and never waits for a password. Host-key checking stays on.
# Adapt the configuration file, not this script.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: fetch-logs.sh --config FILE [--date YYYY-MM-DD | --from YYYY-MM-DD --to YYYY-MM-DD]
                     [--backfill-days N]

Without a date: yesterday, and every day of the last N days (default 7) that has
no marker in the receiving directory yet. Only days before today are fetched.

Configuration file: one source per line, fields separated by blanks:
  NAME  LOGIN@HOST  REMOTE_LOG_DIRECTORY  LOCAL_RECEIVING_DIRECTORY  [SSH_PORT]
Lines starting with # and empty lines are skipped.

Exit status: 0 every requested day arrived or had no files; 1 a day or a source
failed (no marker was placed for it); 2 wrong usage or configuration.
EOF
}

die() {
    printf 'fetch-logs: %s\n' "$1" >&2
    exit "${2:-2}"
}

say() {
    printf 'fetch-logs: %s\n' "$*"
}

config='' single='' first='' last='' backfill=7
while (($#)); do
    case "$1" in
        -h | --help)
            usage
            exit 0
            ;;
        --config | --date | --from | --to | --backfill-days)
            [[ $# -ge 2 ]] || die "missing value for $1"
            case "$1" in
                --config) config=$2 ;;
                --date) single=$2 ;;
                --from) first=$2 ;;
                --to) last=$2 ;;
                --backfill-days) backfill=$2 ;;
            esac
            shift 2
            ;;
        *) die "unknown option: $1" ;;
    esac
done
[[ -n $config && -f $config ]] || die '--config FILE is required'
[[ $backfill =~ ^[0-9]{1,3}$ ]] || die '--backfill-days must be a number'
[[ -z $single || (-z $first && -z $last) ]] || die 'give either --date or --from/--to'
[[ (-z $first && -z $last) || (-n $first && -n $last) ]] || die '--from and --to go together'

today=$(date +%F)
valid_day() {
    [[ $1 =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && [[ $(date -d "$1" +%F 2>/dev/null) == "$1" ]]
}
requested=()
if [[ -n $single ]]; then
    valid_day "$single" || die 'invalid --date'
    requested=("$single")
elif [[ -n $first ]]; then
    if ! valid_day "$first" || ! valid_day "$last"; then
        die 'invalid --from or --to'
    fi
    [[ ! $first > $last ]] || die '--from is after --to'
    day=$first
    while [[ ! $day > $last ]]; do
        requested+=("$day")
        day=$(date -d "$day + 1 day" +%F)
    done
fi
for day in "${requested[@]}"; do
    [[ $day < $today ]] || die "only days before today are fetched: $day"
done

# One instance at a time: two copies into the same directory would trip each other.
exec 9<"$config"
flock -n 9 || die 'another fetch-logs is running' 1

# BatchMode: never ask for anything. An unknown or changed host key is refused, not accepted.
ssh_options=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10)
failures=0

# Print "SIZE NAME" for the log files of one day on the source, sorted by name.
remote_list() { # login port directory day
    local output
    output=$(ssh "${ssh_options[@]}" -p "$2" "$1" \
        "find '$3' -maxdepth 1 -type f -name 'gpdb-$4_*' -printf '%s %f\n'" </dev/null) || return $?
    printf '%s\n' "$output" | grep -E "^[0-9]+ gpdb-$4_[0-9]{6}\.csv(\.[0-9]+)?\$" | sort -k2 || true
}

fetch_day() { # name login port remote local day
    local name=$1 login=$2 port=$3 remote=$4 local_dir=$5 day=$6
    local listing again size file bytes=0 count=0 status
    listing=$(remote_list "$login" "$port" "$remote" "$day") || {
        status=$?
        say "source=$name date=$day state=failed reason=source_unreachable"
        return "$status"
    }
    if [[ -z $listing ]]; then
        say "source=$name date=$day state=no_files"
        return 0
    fi
    # A file here that the source does not have is not ours to explain: no marker.
    for file in "$local_dir"/gpdb-"$day"_*; do
        [[ -e $file ]] || continue
        if ! awk -v wanted="${file##*/}" '$2 == wanted { found = 1 } END { exit !found }' <<<"$listing"; then
            say "source=$name date=$day state=failed reason=local_file_not_on_source file=${file##*/}"
            return 1
        fi
    done
    while read -r size file; do
        if [[ -f $local_dir/$file && $(stat -c %s "$local_dir/$file") == "$size" ]]; then
            bytes=$((bytes + size)) count=$((count + 1))
            continue
        fi
        # Copy under another name, compare the size, only then give the file its name.
        rm -f -- "$local_dir/.$file.part"
        if ! scp -q "${ssh_options[@]}" -P "$port" "$login:$remote/$file" "$local_dir/.$file.part" </dev/null; then
            rm -f -- "$local_dir/.$file.part"
            say "source=$name date=$day state=failed reason=copy_failed file=$file"
            return 1
        fi
        if [[ $(stat -c %s "$local_dir/.$file.part") != "$size" ]]; then
            rm -f -- "$local_dir/.$file.part"
            say "source=$name date=$day state=failed reason=size_mismatch file=$file"
            return 1
        fi
        mv -f -- "$local_dir/.$file.part" "$local_dir/$file"
        bytes=$((bytes + size)) count=$((count + 1))
    done <<<"$listing"
    # The day is over on the source, so its list must be what it was before the copy.
    again=$(remote_list "$login" "$port" "$remote" "$day") || {
        say "source=$name date=$day state=failed reason=source_unreachable"
        return 1
    }
    if [[ $again != "$listing" ]]; then
        say "source=$name date=$day state=failed reason=source_changed_during_copy"
        return 1
    fi
    : >"$local_dir/$day.complete"
    say "source=$name date=$day state=complete files=$count bytes=$bytes"
}

sources=0
while read -r name login remote local_dir port extra <&3; do
    [[ -n $name && $name != \#* ]] || continue
    [[ -z $extra && -n $local_dir ]] || die "configuration line of $name needs four fields and an optional port"
    port=${port:-22}
    [[ $name =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid source name: $name"
    [[ $login =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$ ]] || die "source $name: login must be USER@HOST"
    [[ $remote =~ ^/[A-Za-z0-9._/@+-]*$ ]] || die "source $name: remote directory must be an absolute path of plain characters"
    [[ $port =~ ^[0-9]{1,5}$ ]] || die "source $name: invalid port"
    [[ -d $local_dir && -w $local_dir ]] || die "source $name: receiving directory is missing or not writable"
    sources=$((sources + 1))
    days=("${requested[@]}")
    if ((${#days[@]} == 0)); then
        for ((back = backfill > 1 ? backfill : 1; back >= 1; back--)); do
            day=$(date -d "$today - $back day" +%F)
            [[ -e $local_dir/$day.complete ]] || days+=("$day")
        done
    fi
    for day in "${days[@]}"; do
        status=0
        fetch_day "$name" "$login" "$port" "$remote" "$local_dir" "$day" || status=$?
        if ((status != 0)); then
            failures=$((failures + 1))
            # 255: ssh could not connect or log in. Further days of this source would fail the same way.
            ((status != 255)) || break
        fi
    done
done 3<"$config"
((sources > 0)) || die 'the configuration lists no source'
((failures == 0)) || exit 1
