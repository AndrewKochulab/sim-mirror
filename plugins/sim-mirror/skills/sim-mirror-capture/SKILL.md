---
name: sim-mirror-capture
description: Save screenshots or record app demos, bug reproductions and review evidence with SimMirror. Use for sim_screenshot save_to and sim_record; creating local evidence does not itself authorize uploading, committing or posting it.
---

# Capture verified evidence

Use available tools and live schemas. Identify device/app and the state to demonstrate. If source provenance matters,
check the build's source and `sim_app info`; report when no stamp is available.

1. For video, inspect `sim_record status` before preparing the screen. Unknown ownership blocks UI changes and
   starting/stopping until clarified; a label alone does not establish ownership. Prepare through snapshots and
   grounded actions. Avoid private notifications, credentials and
   unrelated accounts. On a phone, check health for the capabilities needed.
2. For a still, use `sim_screenshot` with appropriate width/region and `save_to` in an allowed project/recording
   folder. `return_image: false` saves without image context when inspection is unnecessary. Inspect the image
   before claiming visual correctness. `frames` samples motion; it is not a timed video.
3. Recheck recording status before starting video. Start with a clear `label`,
   suitable `max_seconds`, and `preset: "pr"` for a small review MP4; use `format: "gif"` or `"both"` when requested.
   `also: "pr"` keeps a small copy alongside the original. Disclose accelerated playback for timing evidence.
4. Perform the flow with meaningful waits. A successful tap does not prove the destination was reached. On failure,
   inspect state before retrying; keep/report useful failure evidence instead of claiming success.
5. Stop a recording this task started, including after walkthrough failure. Rendering can continue after `stop`:
   follow `status`/`list` at bounded intervals until complete. Never copy future paths or partial files. A recording
   that reached its time limit can still be collected with `stop`.
6. Report completed paths, formats, sizes and what the evidence demonstrates. Restore task-changed settings;
   SimMirror manages the recording's demo status bar. On render failure, preserve the reported raw recording.

Upload, commit or post only when requested. Local paths are not public attachments. Use a supported upload interface
and verify the attachment, or give the person the local path to attach. Do not promise `gh` or a filesystem link
embeds a video in a PR.
