#!/bin/bash
# Platform template: paste into a COPY of 文件备份到中转机_Linux V1.0.
# No local writes, including temporary files. appUser still runs ssh/scp on master.
# Platform-supplied environment variables retain the original names.
# shellcheck disable=SC2154
set -o pipefail
echo "begin file full backup"
dest_path=$RCLONE_DEST_PATH
echo "dest_path=$dest_path"
bucketName=$RCLONE_BUCKET_NAME
echo "bucketName:$bucketName"
planId=$PLAN_ID
echo "planId:$planId"
echo "backupFilePath:$backupFilePath"
echo "appUser:$appUser"

fail() {
    echo "push logs error: $1"
    exit "${2:-1}"
}

[[ -n $backHomePath && -n $bucketName && -n $dest_path ]] || fail 'backHomePath, bucketName and dest_path must not be empty'
pushDays=${pushDays:-7}
[[ $pushDays =~ ^[0-9]{1,3}$ ]] || fail 'pushDays must be an integer from 1 to 366'
pushDays=$((10#$pushDays))
((pushDays >= 1 && pushDays <= 366)) || fail 'pushDays must be an integer from 1 to 366'
today=$(date +%F) || exit $?
first=$(date -d "$today - $pushDays days" +%F) || exit $?
master=$(hostname) || exit $?

# Freeze names and sizes before connecting. Whitespace separates directories, as
# in the original template. Globs are used only to list files, never to write.
read -r -a backupArray <<<"$backupFilePath"
files=() sizes=()
declare -A seen=() dayCount=() dayBytes=()
shopt -s nullglob
for pathStr in "${backupArray[@]}"; do
    if [[ ! -d $pathStr ]]; then
        echo "path:$pathStr does not exist.Continue to backup the next path."
        continue
    fi
    echo "------------backup path:$pathStr start------------------------------"
    for file in "$pathStr"/gpdb-*.csv*; do
        [[ -f $file && ! -L $file ]] || continue
        name=${file##*/}
        [[ $name =~ ^gpdb-([0-9]{4}-[0-9]{2}-[0-9]{2})_[0-9]{6}\.csv(\.[0-9]+)?$ ]] || continue
        day=${BASH_REMATCH[1]}
        [[ $day < $today && ! $day < $first ]] || continue
        [[ $(date -d "$day" +%F 2>/dev/null) == "$day" ]] || continue
        [[ ! ${seen[$name]+present} ]] || fail "duplicate file name: $name"
        seen[$name]=1
        size=$(stat -c %s -- "$file") || exit $?
        files+=("$file") sizes+=("$size")
        dayCount[$day]=$((${dayCount[$day]:-0} + 1))
        dayBytes[$day]=$((${dayBytes[$day]:-0} + size))
    done
    echo "------------backup path:$pathStr end------------------------------"
done

fullbkPath=${backHomePath}/$bucketName/$dest_path
echo "fullbkPath=$fullbkPath"
echo "create directory $fullbkPath in transfer machine."
remote() {
    local command
    printf -v command '%q ' ssh "sfmon@$uploadServerIp" "$1"
    su - "$appUser" -c "$command"
}
printf -v quoted '%q' "$fullbkPath"
# Same remote mkdir and rm -fr DIRECTORY/* as the original template. A failed
# mkdir must not be hidden by a successful rm. There is no local rm or mkdir.
remote "mkdir -p $quoted && rm -fr $quoted/*" || fail "Cann't create directory $fullbkPath in transfer machine sfmon@$uploadServerIp. And exit." "$?"

printf -v manifest 'SQL_APM_PUSH\t1\nhost\t%s\ntoday\t%s\ndays\t%s\n' "$master" "$today" "$pushDays"
total=0
for index in "${!files[@]}"; do
    file=${files[$index]} name=${files[$index]##*/} size=${sizes[$index]}
    echo "push files to transfer machine sfmon@$uploadServerIp:$fullbkPath file=$name bytes=$size"
    printf -v command '%q ' scp -p "$file" "sfmon@$uploadServerIp:$fullbkPath/"
    su - "$appUser" -c "$command" </dev/null
    exitCode=$?
    if [[ $exitCode != 0 ]]; then
        fail "pathStr:$file scp error" "$exitCode"
    fi
    echo "pathStr:$file scp ok"
    printf -v manifest '%sfile\t%s\t%s\n' "$manifest" "$name" "$size"
    total=$((total + size))
done
# The manifest lives in shell memory on master. Only the remote process writes
# the temporary marker; a successful rename is the completion signal.
printf '%s' "$manifest" | remote "cat > $quoted/push.complete.tmp && mv -f $quoted/push.complete.tmp $quoted/push.complete"
exitCode=$?
[[ $exitCode == 0 ]] || fail 'completion marker write failed' "$exitCode"
for day in "${!dayCount[@]}"; do
    echo "date=$day state=pushed files=${dayCount[$day]} bytes=${dayBytes[$day]}"
done
echo "state=complete today=$today pushDays=$pushDays files=${#files[@]} bytes=$total"
exit 0
