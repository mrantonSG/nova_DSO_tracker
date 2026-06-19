# Nova Release Checklist

## Before every release — run locally
- [ ] Full test suite passes: `python -m pytest tests/ -v`
- [ ] Dashboard loads for Singapore location — objects visible, max altitude non-zero
- [ ] Graph view for any object — moon %, moon sep, astro dusk/dawn populated
- [ ] Outlook tab loads and completes (not stuck on "waiting") for at least one location

## After deploy to VPS — verify before announcing
- [ ] `docker logs nova_app --tail 30` — no FATAL ERROR or Status: error lines
- [ ] Dashboard loads for Bad Fischau — max altitude non-zero, observable minutes present
- [ ] Graph view, London location, June date — moon % and moon sep populated (not N/A)
- [ ] Outlook tab, Bad Fischau — completes within 90 seconds, shows opportunities
- [ ] Outlook tab, London location — loads (may show 0 observable but should not hang)
- [ ] Journal page loads
- [ ] Config page loads, save a location — no errors

## Red flags in logs to watch for
- `FATAL ERROR` in any worker
- `Status: error` in outlook worker
- `RepresenterError` or `yaml` errors
- `strptime` ValueError
- Any uncaught Python traceback