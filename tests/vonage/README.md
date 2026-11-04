# Vonage — not tested (node 1: record the intake call)

**Status:** Not tested — no Vonage license available for this project.

## Protocol

In the workflow, node 1 records the volunteer–client intake call through Vonage, after the client gives consent, and passes the recording to AssemblyAI for transcription (node 2).

## Why there are no unit tests

Black-box tests of this protocol would have to place and record real calls through a Vonage account. This project has no Vonage license, and the test suite must run on a free GitHub Actions runner without paid services, so the protocol cannot be exercised.

## How the gap is covered

- **Downstream input is tested directly.** The AssemblyAI tests (`tests/assemblyai`) use synthetic recordings made with Voice Memos in place of Vonage recordings, including an 8 kHz telephony-quality file and a stereo file downmixed to mono, so node 2 is tested against the kinds of audio a phone recording is likely to produce.
- **The handoff is a file.** Node 2 only needs an audio file. Any recording Vonage produces can be checked against the formats the AssemblyAI tests already cover.

## What testing would need

With a license, tests could check that a recording is produced for a completed call, that its format and length match the call, and that no recording is made without consent.
