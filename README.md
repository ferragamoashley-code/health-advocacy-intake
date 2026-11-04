# Health Advocacy Intake

Black-box unit tests for the atomic protocols in an AI-assisted intake workflow for health advocacy nonprofits.

See the [wire diagram](docs/README.md) for the full workflow.

## Protocols

| Protocol | Status | Details |
|---|---|---|
| AssemblyAI (transcription) | Complete (46 passed, 5 skipped) | [tests/assemblyai](tests/assemblyai/README.md) |
| Pandera (schema validation) | Complete (26 passed) | [tests/pandera](tests/pandera/README.md) |
   | Presidio (de-identification) | Complete (31 passed) | [tests/presidio](tests/presidio/README.md) |
| Vonage (call recording) | Not tested (no license) | [tests/vonage](tests/vonage/README.md) |

## Setup

More to come.