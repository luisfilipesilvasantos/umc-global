# Espera o servidor, faz POST do workflow e faz poll do history ate terminar.
$ErrorActionPreference = "Stop"
$wf = Get-Content "C:\Users\luisf\AppData\Local\Temp\opencode\umc_v3_wf.json" -Raw
$deadline = (Get-Date).AddMinutes(4)
while ($true) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8188/system_stats" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { break }
    } catch { }
    if ((Get-Date) -gt $deadline) { Write-Output "SERVER_NAO_ARRANCOU"; exit 2 }
    Start-Sleep -Seconds 3
}
Write-Output "SERVER OK"
$body = @{ prompt = ($wf | ConvertFrom-Json) } | ConvertTo-Json -Depth 64
try {
    $post = Invoke-RestMethod -Uri "http://127.0.0.1:8188/prompt" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 30
} catch {
    Write-Output "POST FALHOU: $($_.Exception.Message)"
    if ($_.ErrorDetails) { Write-Output $_.ErrorDetails.Message }
    exit 3
}
$pid1 = $post.prompt_id
Write-Output "PROMPT_ID=$pid1"
$deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:8188/history/$pid1" -TimeoutSec 10
    $entry = $h.$pid1
    if ($entry) {
        $st = $entry.status
        Write-Output "STATUS=$($st.status_str) COMPLETED=$($st.completed)"
        foreach ($k in $st.messages) {
            $mtype = $k[0]; $mp = $k[1]
            if ($mtype -eq "execution_error") {
                Write-Output "ERRO no node $($mp.node_id): $($mp.exception_message)"
                Write-Output $mp.traceback
            }
        }
        if ($st.completed) {
            $imgs = $entry.outputs.PSObject.Properties | ForEach-Object { $_.Value.images } | Where-Object { $_ }
            Write-Output ("IMAGENS=" + (($imgs | ForEach-Object { "$($_.subfolder)\$($_.filename)" }) -join ", "))
            exit 0
        }
        exit 4
    }
}
Write-Output "TIMEOUT"
exit 5
