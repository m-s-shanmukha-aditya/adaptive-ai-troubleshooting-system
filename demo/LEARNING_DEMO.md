# Cloud-learning demo runbook

1. **Prerequisites.** Start from a clean `git status` on `refactor/generic-engine`.
   Have Ollama running with `mistral` available. Set these environment variable
   names in your own terminal: `ENABLE_CLOUD_LEARNING=1`,
   `ENABLE_PROVISIONAL_BLUEPRINTS=1`, `ENABLE_RULE_OBSERVATIONS=1`,
   `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, and `OPENROUTER_API_KEY`
   (set the key only in your own terminal; it is required for cloud learning).
   `OPENROUTER_MODEL` is optional if you want to use a stronger model instead
   of the free default. Do not place the key in a file, command history, or demo
   output.
2. **Start the app.** From the repository root, use the documented command:
   set the non-secret flags in PowerShell, configure `OPENROUTER_API_KEY` in
   your own terminal without echoing its value, then run:

   ```powershell
   $env:ENABLE_CLOUD_LEARNING = "1"
   $env:ENABLE_PROVISIONAL_BLUEPRINTS = "1"
   $env:ENABLE_RULE_OBSERVATIONS = "1"
   $env:HF_HUB_OFFLINE = "1"
   $env:TRANSFORMERS_OFFLINE = "1"
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

   `OPENROUTER_MODEL` may also be set in this terminal. Keep the app terminal
   running.
3. **Run the learning pass.** In another terminal at the repository root, run
   `python demo/learning_demo.py --learn`. This sends each synthetic issue once,
   then repeats only for scenarios whose new provisional draft appeared.
4. **Review every draft.** Run
   `python -m app.tools.blueprint_review list`, then
   `python -m app.tools.blueprint_review show <id>` for each listed ID. Read
   every draft fully before deciding. Run
   `python -m app.tools.blueprint_review approve <id>` to approve it or
   `python -m app.tools.blueprint_review reject <id>` to reject it. Approval
   moves the file into `data/blueprints/`; the running app needs a restart to
   load approved blueprints.
5. **Restart the app.** Stop the app process, then start it again using the
   same environment variables and command from step 2.
6. **Check after approval.** Run
   `python demo/learning_demo.py --after-approval`. It sends one request only
   for each scenario with a draft recorded in `demo/outputs_learning/state.json`.
7. **Stop local services.** Stop the app and Ollama when the demo is complete.
8. **Clean up generated blueprints.** Preserve the drafts recorded in
   `demo/outputs_learning/state.json` as local evidence, then remove only those
   demo-generated JSON files from the runtime data directories. This leaves
   pre-existing approved blueprints untouched and keeps each directory marker:

   ```powershell
   New-Item -ItemType Directory -Force demo/outputs_learning/blueprints/approved | Out-Null
   New-Item -ItemType Directory -Force demo/outputs_learning/blueprints/rejected | Out-Null
   $drafts = Get-Content -Raw demo/outputs_learning/state.json | ConvertFrom-Json
   foreach ($entry in $drafts.PSObject.Properties) {
       $filename = $entry.Value
       foreach ($source in @(
           @{ Directory = "data/blueprints"; Evidence = "demo/outputs_learning/blueprints/approved" },
           @{ Directory = "data/blueprints_rejected"; Evidence = "demo/outputs_learning/blueprints/rejected" }
       )) {
           $path = Join-Path $source.Directory $filename
           if (Test-Path -LiteralPath $path -PathType Leaf) {
               Copy-Item -LiteralPath $path -Destination $source.Evidence
           }
       }
       foreach ($directory in @("data/blueprints", "data/blueprints_provisional", "data/blueprints_rejected")) {
           $path = Join-Path $directory $filename
           if (Test-Path -LiteralPath $path -PathType Leaf) {
               Remove-Item -LiteralPath $path
           }
       }
   }
   ```

   Run the full test suite from the repository root:
   `python -m pytest -q -p no:cacheprovider tests`. Do not stage or commit
   `data/experience.jsonl` or `data/cloud_learning.jsonl`; retain any resulting
   log changes locally.
9. **Privacy.** Use only the synthetic reports in
   `demo/learning_requests.json`. The app redacts reports before sending them
   to the cloud-learning provider. The demo client sends only the scenario
   issue text to the local app; never add real reports, credentials, or keys
   to the scenario file or outputs.
10. **Troubleshooting.**
    - `unexpected_hit`: retrieval matched an existing blueprint, so this run
      cannot demonstrate learning. Choose a scenario farther from PostgreSQL
      or HTTP-gateway topics.
    - `no_draft`: inspect reason codes in `data/cloud_learning.jsonl`. The free
      default model may not produce valid blueprint JSON; set
      `OPENROUTER_MODEL` to a stronger model and try again.
    - The app allows 10 cloud-learning calls per hour. Restarting the app resets
      its in-process counter. The script also limits itself to 15 HTTP requests
      per run and never retries a failed request.
