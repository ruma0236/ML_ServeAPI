import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";


const sessionRoot = process.env.EVM_LIVE_LIFECYCLE_SESSION_ROOT ||
  "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/control-panel-live-production-lifecycle/2026-08-12/sessions/smolvlm-20260812t141036z";
const ffmpegPath = process.env.EVM_FFMPEG_PATH ||
  "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/control-panel-editorial-refresh/2026-08-06/video-tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe";
const sourcePath = path.join(
  sessionRoot,
  "video",
  "final",
  "smolvlm-live-local-production-lifecycle-60fps.mp4"
);
const finalRoot = path.join(sessionRoot, "video", "final");
const outputPath = path.join(finalRoot, "smolvlm-live-local-production-lifecycle-60fps-editorial-v2.mp4");
const subtitlePath = path.join(finalRoot, "smolvlm-live-local-production-lifecycle-ko-v2.ass");
const manifestPath = path.join(sessionRoot, "editorial-edit-manifest-v2.json");
const ffmpegLogPath = path.join(sessionRoot, "logs", "ffmpeg-editorial-edit-v2.log");
const contactSheetPath = path.join(finalRoot, "editorial-v2-timeline-contact-sheet.png");
const seekProofPath = path.join(finalRoot, "editorial-v2-seek-proof.png");
const lateCaptionProofs = [145, 154].map((second) => ({
  second,
  path: path.join(finalRoot, `editorial-v2-caption-proof-${second}s.png`)
}));
const transitionSeconds = 0.25;

// Only confirmed sub-0.7-second refresh/navigation flashes are omitted. Every lifecycle action remains in order.
const keptSegments = [
  { source_start: 0.000, source_end: 51.483, reason: "launch through staging approval" },
  { source_start: 52.050, source_end: 60.033, reason: "staging serving after scroll reset" },
  { source_start: 60.600, source_end: 82.983, reason: "staging observation after refresh flash" },
  { source_start: 83.533, source_end: 93.583, reason: "release evidence and production request" },
  { source_start: 94.117, source_end: 98.217, reason: "production pending approval" },
  { source_start: 98.750, source_end: 101.617, reason: "production approval accepted" },
  { source_start: 102.167, source_end: 123.333, reason: "production applying" },
  { source_start: 123.867, source_end: 133.150, reason: "production applied" },
  { source_start: 133.300, source_end: 142.370, reason: "MLflow loading and exact-run evidence" },
  { source_start: 142.559, source_end: 147.600, reason: "production readiness evidence" },
  { source_start: 147.733, source_end: 156.383, reason: "Prometheus loading and target evidence" },
  { source_start: 156.433, source_end: 166.150, reason: "Prometheus target evidence and Control Panel navigation" },
  { source_start: 166.233, source_end: 172.330, reason: "loaded final Control Panel summary" }
];

const captions = [
  [0.000, 5.703, "ScienceQA 데이터셋과 SmolVLM을 선택합니다. 이번 Run은 32/8/8 샘플로 파이프라인 전체 흐름을 검증합니다."],
  [5.703, 9.270, "새 Run ID를 발급합니다. 이후 데이터·모델·승인·배포 이력을 이 ID로 추적합니다."],
  [9.270, 14.186, "단일 GPU를 사용 중인 EfficientNet-B0를 확인하고, SmolVLM 작업에 GPU를 넘길 준비를 합니다."],
  [14.186, 17.604, "승인된 B0 Deployment만 잠시 내리고, 이 Run 전용 GPU lease를 확보합니다."],
  [17.604, 31.187, "RTX 4080에서 SmolVLM LoRA 학습과 baseline·held-out 평가를 실제로 실행합니다."],
  [31.187, 43.871, "학습 step과 loss가 실시간으로 갱신됩니다. 화면 값은 실행 artifact와 동일합니다."],
  [43.871, 52.020, "자동 검증은 통과했습니다. 스테이징 전환은 요청자와 분리된 승인자의 확인을 기다립니다."],
  [52.020, 60.570, "승인된 adapter를 스테이징에 로드하고 ScienceQA 이미지로 실제 CUDA 추론을 수행합니다."],
  [60.570, 83.621, "Prometheus가 스테이징 endpoint를 정상 수집하는지 확인한 뒤 임시 GPU lease를 반납합니다."],
  [83.621, 88.671, "정확도 75%, parse rate 100%와 latency·VRAM을 함께 확인해 release gate를 판정합니다."],
  [88.671, 98.737, "검증 artifact와 CI 결과를 다시 확인한 뒤 프로덕션 배포 요청을 만들고 별도 승인을 받습니다."],
  [98.737, 123.853, "승인된 모델 identity를 한 번 더 대조한 뒤 local-production serving을 시작합니다."],
  [123.853, 133.150, "SmolVLM 배포가 완료됐습니다. UI 상태, readiness, CUDA 추론, 모니터링 결과가 모두 일치합니다."],
  [133.300, 142.370, "MLflow로 이동해 이번 Run의 파라미터, metric, adapter artifact를 독립적으로 확인합니다."],
  [142.559, 147.550, "/ready 응답에서 모델 revision, adapter digest, 데이터 identity, CUDA 상태를 확인합니다."],
  [147.733, 156.383, "Prometheus로 이동해 새 프로덕션 endpoint가 UP 상태로 수집되는지 확인합니다."],
  [156.433, 172.330, "Control Panel로 돌아왔습니다. 데이터 수집부터 배포·모니터링까지 하나의 Run으로 완료했습니다."]
];
const editedCaptions = mapCaptionsToEditedTimeline(captions, keptSegments, transitionSeconds);

await mkdir(finalRoot, { recursive: true });
await writeFile(subtitlePath, buildAss(editedCaptions), "utf8");

const filterGraph = buildFilterGraph(keptSegments, transitionSeconds, subtitlePath);
const editResult = run(ffmpegPath, [
  "-y",
  "-i", sourcePath,
  "-f", "lavfi",
  "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
  "-filter_complex", filterGraph,
  "-map", "[vout]",
  "-map", "1:a:0",
  "-c:v", "h264_nvenc",
  "-preset", "p4",
  "-tune", "hq",
  "-rc", "vbr",
  "-cq", "18",
  "-b:v", "0",
  "-pix_fmt", "yuv420p",
  "-r", "60",
  "-g", "120",
  "-sc_threshold", "0",
  "-c:a", "aac",
  "-b:a", "128k",
  "-shortest",
  "-movflags", "+faststart",
  outputPath
]);
await writeFile(ffmpegLogPath, editResult.stderr, "utf8");
if (editResult.status !== 0) {
  throw new Error(`Editorial encode failed with exit code ${editResult.status}`);
}

const expectedDuration = keptSegments.reduce(
  (total, segment) => total + segment.source_end - segment.source_start,
  0
) - transitionSeconds * (keptSegments.length - 1);

const mediaProbe = run(ffmpegPath, ["-hide_banner", "-i", outputPath]);
const durationSeconds = parseDuration(mediaProbe.stderr);
const faststart = await hasFrontLoadedMoov(outputPath);

const contactResult = run(ffmpegPath, [
  "-y", "-i", outputPath,
  "-vf", "fps=1/12,scale=360:225,tile=4x4",
  "-frames:v", "1", contactSheetPath
]);
if (contactResult.status !== 0) throw new Error("Contact sheet generation failed");

const seekSecond = Math.floor(durationSeconds / 2);
const seekResult = run(ffmpegPath, [
  "-y", "-ss", String(seekSecond), "-i", outputPath,
  "-frames:v", "1", seekProofPath
]);
if (seekResult.status !== 0) throw new Error("Seek proof generation failed");

for (const proof of lateCaptionProofs) {
  const result = run(ffmpegPath, [
    "-y", "-ss", String(proof.second), "-i", outputPath,
    "-frames:v", "1", "-update", "1", proof.path
  ]);
  if (result.status !== 0) throw new Error(`Caption proof generation failed at ${proof.second}s`);
}

const sourceManifest = JSON.parse(await readFile(path.join(sessionRoot, "recording-manifest.json"), "utf8"));
const manifest = {
  schema_version: "evm.smolvlm_lifecycle_editorial_edit.v2",
  edited_at: new Date().toISOString(),
  source: {
    path: sourcePath,
    sha256: await sha256File(sourcePath),
    run_id: sourceManifest.run_id,
    execution_revision: sourceManifest.execution_revision
  },
  output: {
    path: outputPath,
    sha256: await sha256File(outputPath),
    duration_seconds: durationSeconds,
    expected_duration_seconds: Number(expectedDuration.toFixed(3)),
    resolution: "1440x900",
    frame_rate: 60,
    video_codec: "H.264",
    audio_codec: "AAC",
    faststart,
    seek_proof: seekProofPath,
    contact_sheet: contactSheetPath,
    late_caption_proofs: lateCaptionProofs
  },
  edit_policy: {
    original_preserved: true,
    rerecorded: false,
    lifecycle_actions_removed: false,
    refresh_flashes_removed: true,
    loading_screens_removed: false,
    removed_source_seconds: Number(invertSegments(keptSegments, 172.330).reduce(
      (total, segment) => total + (segment.source_end - segment.source_start),
      0
    ).toFixed(3)),
    dissolve_overlap_seconds: Number((transitionSeconds * (keptSegments.length - 1)).toFixed(3)),
    transition: "250 ms dissolve between stable views",
    subtitle_policy: "Original baked captions are covered per page and replaced immediately with compact Korean operator-facing explanations."
  },
  kept_segments: keptSegments,
  removed_segments: invertSegments(keptSegments, 172.330),
  captions: editedCaptions.map(([edited_start, edited_end, text]) => ({ edited_start, edited_end, text })),
  validation: {
    duration_matches: Math.abs(durationSeconds - expectedDuration) <= 0.15,
    declared_60_fps: /60 fps/.test(mediaProbe.stderr),
    h264: /Video: h264/.test(mediaProbe.stderr),
    aac: /Audio: aac/.test(mediaProbe.stderr),
    faststart,
    caption_visible_at_145s: captionCovers(editedCaptions, 145),
    caption_visible_at_154s: captionCovers(editedCaptions, 154)
  }
};
await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");

if (!Object.values(manifest.validation).every(Boolean)) {
  throw new Error(`Editorial media validation failed: ${JSON.stringify(manifest.validation)}`);
}

console.log(JSON.stringify({ status: "pass", output: outputPath, manifest: manifestPath, validation: manifest.validation }, null, 2));


function buildFilterGraph(segments, transition, assPath) {
  const escapedAssPath = assPath.replaceAll("\\", "/").replace(":", "\\:").replaceAll("'", "\\'");
  const splitOutputs = segments.map((_, index) => `[source${index}]`).join("");
  const filters = [
    "[0:v]fps=60,settb=AVTB" +
      ",drawbox=x=348:y=808:w=744:h=70:color=0x050605@1.0:t=fill:enable='between(t,0,133.150)+between(t,166.223,172.330)'" +
      ",drawbox=x=348:y=808:w=744:h=70:color=0x11181d@1.0:t=fill:enable='between(t,136.872,142.370)'" +
      ",drawbox=x=348:y=808:w=744:h=70:color=0x111111@1.0:t=fill:enable='between(t,142.559,147.600)'" +
      ",drawbox=x=348:y=808:w=744:h=70:color=0x181d20@1.0:t=fill:enable='between(t,151.333,156.383)'[clean]",
    `[clean]split=${segments.length}${splitOutputs}`
  ];

  segments.forEach((segment, index) => {
    filters.push(
      `[source${index}]trim=start=${segment.source_start}:end=${segment.source_end},setpts=PTS-STARTPTS,fps=60,settb=AVTB[segment${index}]`
    );
  });

  let accumulatedDuration = segments[0].source_end - segments[0].source_start;
  let previous = "segment0";
  for (let index = 1; index < segments.length; index += 1) {
    const output = `transition${index}`;
    const offset = accumulatedDuration - transition;
    filters.push(
      `[${previous}][segment${index}]xfade=transition=fade:duration=${transition}:offset=${offset.toFixed(3)}[${output}]`
    );
    accumulatedDuration += segments[index].source_end - segments[index].source_start - transition;
    previous = output;
  }
  filters.push(`[${previous}]ass='${escapedAssPath}'[vout]`);
  return filters.join(";");
}


function mapCaptionsToEditedTimeline(entries, segments, transition) {
  const mapped = [];
  let editedSegmentStart = 0;
  segments.forEach((segment, segmentIndex) => {
    entries.forEach(([captionStart, captionEnd, text]) => {
      const overlapStart = Math.max(captionStart, segment.source_start);
      const overlapEnd = Math.min(captionEnd, segment.source_end);
      if (overlapEnd - overlapStart < 0.05) return;
      let editedStart = editedSegmentStart + overlapStart - segment.source_start;
      let editedEnd = editedSegmentStart + overlapEnd - segment.source_start;
      if (segmentIndex < segments.length - 1) {
        const transitionStart = editedSegmentStart + segment.source_end - segment.source_start - transition;
        editedEnd = Math.min(editedEnd, transitionStart);
      }
      if (editedEnd - editedStart >= 0.35) mapped.push([editedStart, editedEnd, text]);
    });
    editedSegmentStart += segment.source_end - segment.source_start;
    if (segmentIndex < segments.length - 1) editedSegmentStart -= transition;
  });
  return mapped.reduce((result, entry) => {
    const previous = result.at(-1);
    if (previous && previous[2] === entry[2] && entry[0] - previous[1] <= transition * 2 + 0.2) {
      previous[1] = entry[1];
      return result;
    }
    result.push(entry);
    return result;
  }, []);
}


function buildAss(entries) {
  const header = `[Script Info]\nScriptType: v4.00+\nPlayResX: 1440\nPlayResY: 900\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Malgun Gothic,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,10,0,2,60,60,31,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n`;
  const events = entries.map(([start, end, text]) =>
    `Dialogue: 0,${assTime(start)},${assTime(end)},Default,,0,0,0,,${escapeAss(text)}`
  );
  return `${header}${events.join("\n")}\n`;
}


function captionCovers(entries, second) {
  return entries.some(([start, end, text]) => start <= second && second < end && text.trim().length > 0);
}


function assTime(seconds) {
  const centiseconds = Math.round(seconds * 100);
  const hours = Math.floor(centiseconds / 360000);
  const minutes = Math.floor((centiseconds % 360000) / 6000);
  const wholeSeconds = Math.floor((centiseconds % 6000) / 100);
  const fraction = centiseconds % 100;
  return `${hours}:${String(minutes).padStart(2, "0")}:${String(wholeSeconds).padStart(2, "0")}.${String(fraction).padStart(2, "0")}`;
}


function escapeAss(value) {
  return value.replaceAll("\\", "\\\\").replaceAll("{", "\\{").replaceAll("}", "\\}");
}


function invertSegments(segments, sourceEnd) {
  const removed = [];
  for (let index = 1; index < segments.length; index += 1) {
    removed.push({
      source_start: segments[index - 1].source_end,
      source_end: segments[index].source_start,
      duration_seconds: Number((segments[index].source_start - segments[index - 1].source_end).toFixed(3)),
      reason: "sub-0.7-second page refresh or navigation flash"
    });
  }
  if (segments.at(-1).source_end < sourceEnd) {
    removed.push({ source_start: segments.at(-1).source_end, source_end: sourceEnd, reason: "trailing frame" });
  }
  return removed;
}


function run(command, args) {
  return spawnSync(command, args, { encoding: "utf8", windowsHide: true, maxBuffer: 64 * 1024 * 1024 });
}


function parseDuration(stderr) {
  const match = stderr.match(/Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)/);
  if (!match) throw new Error("Could not parse output duration");
  return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
}


async function hasFrontLoadedMoov(filePath) {
  const bytes = await readFile(filePath);
  const moov = bytes.indexOf(Buffer.from("moov"));
  const mdat = bytes.indexOf(Buffer.from("mdat"));
  return moov >= 0 && mdat >= 0 && moov < mdat;
}


async function sha256File(filePath) {
  const bytes = await readFile(filePath);
  return createHash("sha256").update(bytes).digest("hex");
}
