# Returns JSON: title, artist, album, app, thumbnail_path (or empty)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    return $netTask.Result
}

[void][Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime]
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$sessions = $mgr.GetSessions()

$result = @{ playing = $false }
if ($sessions.Count -eq 0) { $result | ConvertTo-Json -Compress; exit }

$session = $null
foreach ($s in $sessions) {
    $info = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.MediaProperties.MusicDisplayProperties])
    if ($info.Title) { $session = $s; break }
}
if (-not $session) { $session = $sessions[0] }

$props = Await ($session.TryGetMediaPropertiesAsync()) ([Windows.Media.MediaProperties.MusicDisplayProperties])
$playback = $session.GetPlaybackInfo()
$status = $playback.PlaybackStatus.ToString()

$thumbPath = ""
if ($props.Thumbnail) {
    $outDir = Join-Path $env:TEMP "nexus-art"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $thumbPath = Join-Path $outDir "cover.jpg"
    $stream = Await ($props.Thumbnail.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
    $reader = [Windows.Storage.Streams.DataReader]::new($stream.GetInputStreamAt(0))
    Await ($reader.LoadAsync($stream.Size)) ([UInt32])
    $bytes = New-Object byte[] $stream.Size
    $reader.ReadBytes($bytes)
    [IO.File]::WriteAllBytes($thumbPath, $bytes)
}

@{
    playing = ($status -eq "Playing")
    status = $status
    title = $props.Title
    artist = $props.Artist
    album = $props.AlbumTitle
    app = $session.SourceAppUserModelId
    thumbnail = $thumbPath
} | ConvertTo-Json -Compress
