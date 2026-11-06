# Hermes local browser voice — 6 November 2026

This is dated operational evidence, not whole-host synchronization. Open
<https://agents.home.arpa/hermes/>, sign in, choose **Start voice conversation**,
grant microphone access and speak. The app transcribes, replies aloud and returns
to listening. **Stop samtalen** ends the session; **End** also stops it.

## Applied source and recovery

- Core speech admission: `d6f47023f26084a27b7ad05035d40058e9592fc6`, applied
  through the dedicated speech-bridge lifecycle; the MCP receipt was retained.
- Guest port/header correction, authenticated relay and boot/watcher lifecycle:
  `e3e63fecd02050fc61622e2c181c29f97b2e58a4`. Dependencies match
  `config/hermes-speech.json`; packaged Webapp archive digest:
  `bada27fa9768ebde950373c2ebe7a3941685cba93542af1f9f8e1b1ddd2b2f06`.
- Core ingress: `e60d7a6b884125f8633a8a16a5022e061978ccf2`. Caddy config digest:
  `b05b0a6e64eedd4731d143b05b7491cc9c38aab7370f34d7cc5c72d0c268406d`.
  The Homepage file and two attached firewall/relay units were retained.

An initial installer error selected NemoClaw's reserved API port 8646 for the
Webapp. Startup failed and OpenShell 0.0.116 retained terminal Error. Webapp now
uses 18796. Both complete private application archives and a native SQLite
backup were retained. Incident tools preserved the original sandbox/container,
corrected only the port/header, and repaired the exact diagnosed phase to
Stopped while offline. Native start established Ready. A later normal managed
stop/start passed, superseding the direct incident restart. No sandbox, policy,
credential, journal or user data was deleted or replaced. The image remains
`sha256:f15b60053b28b681efe555f89a6d170c7f7cd51036ed570b9951908c19f85311`.

The optional usage-statistics offer was declined. Source declares the same
metrics opt-out. Privacy-only reconciliation verifies the original source
preimage and installed assets, retains before-files and refreshes the speech
receipt. Its live plan proved the current opt-out already has the selected
semantics; unrelated configuration changes are refused.

## Browser voice evidence

System Chrome used the real HTTPS entry point, native account, Hermes backend,
local Whisper and local OmniVoice. Synthetic WAV fixtures entered a microphone
MediaStream through the actual VAD, MediaRecorder and transcription path. No
room audio or private conversation was collected. Physical microphone/headset
acoustics and mobile background behavior were not tested.

| Check | Observed result |
| --- | --- |
| Danish input | Whisper HTTP 200; expected Danish assistant reply |
| Danish playback | 96,000 bytes of 24 kHz PCM; nonzero Web Audio buffer completed |
| Rearming | Listening returned after Danish playback |
| English next turn, same session | Whisper HTTP 200; expected English reply; no second start click |
| English playback | 97,440 bytes of 24 kHz PCM; second nonzero buffer completed |
| Rearming | Listening returned after English playback |
| Spoken Danish stop | Third transcription; voice ended; assistant count stayed at two |
| Uncached dashboard loads | Two fresh authenticated contexts; HTTP 200; zero failed requests |
| Forward recovery | Only Webapp forwarder terminated; watcher restored HTTP 401 readiness in 33.08 seconds |
| Account/origin gates | Anonymous config 401; signed-in config 200; foreign-origin voice POST 403 |

The initial module burst also exhausted OpenShell's twenty shared SSH/service
forward connections, causing 502s. Caddy now queues Hermes requests above eight
connections without raising the native limit. Browser voice was exercised again
after this ingress rollout.

## Compatibility and remaining scope

After managed restart, all seven read-only household MCP probes passed with 60
tools discovered. OMH/NemoClaw plugins, all 18 local model routes, bounded
concurrency, native memory and sealed packages passed. Public browsing worked;
private-address browsing was rejected. The dedicated `hermes-agent` Mattermost
bot passed REST/WebSocket authentication; no test message was posted.

Focused Linux speech and ingress tests, full repository validation and source
CI provide implementation evidence, separately from these live checks. Private
receipts/diagnostics remain in the operator's `hermes-speech` state directory and
Core's Hermes ingress/speech-bridge directories. Credentials and recordings are
outside Git.

`homecompute status`, `services`, `doctor` and `drift` were inspected. Core and
Spark were reachable with zero failed host units. Broad deployment markers
remain older than origin/master; dedicated workload rollouts do not establish
whole-host synchronization. No broad rollout was performed. Retired records and
the aborted native recreate journal were preserved.
