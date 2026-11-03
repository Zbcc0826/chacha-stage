# Stage and desktop pet

This package is a dsh bundle. It registers two tools, `stage_say` and `stage_status`, against the stage already running on your machine.

## What you need running

Start the stage before the tools are useful:

```text
pip install -r requirements.txt
python server.py
```

Open `http://127.0.0.1:8787/`. The bundle does not start that process and does not ship the character model. The livestream identity code is entered on the connection page like a key and stays on that machine.

## Tools

`stage_say` speaks one sentence. Call it again for the next sentence. Playback of an earlier sentence does not wait for a later call.

`stage_status` reports the loaded model and whether a dsh key is connected. It does not return the key.

## Install

From a profile that should see the tools:

```sh
dsh plugin --profile headless add github:Zbcc0826/chacha-stage
```

The package is plain JavaScript, so a git install does not need a build script or `allowBuilds`. The patch layer is `cordis.patch.yml`, declared by `dsh.bundle`. It is applied after `@deepseek-ai/dsh-base`, and your profile patch can still override the `chacha-stage` row.
