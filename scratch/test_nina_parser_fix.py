from nova.log_parser import parse_nina_log

# Test with partial log (no success message for first AF run)
test_log = """2026-03-06T18:20:47.5530|INFO|FocuserMediator.cs|BroadcastAutoFocusRunStarting|92|Autofocus starting notification received
2026-03-06T18:20:47.5560|INFO|FocuserVM.cs|MoveFocuserInternal|216|Moving Focuser to position 6250
2026-03-06T18:20:51.5550|INFO|CameraVM.cs|Capture|742|Starting Exposure - Exposure Time: 4s; Filter: Lum; Gain: 100; Offset 50; Binning: 1x1;
2026-03-06T18:20:56.3660|INFO|FocuserVM.cs|MoveFocuserInternal|216|Moving Focuser to position 6200
2026-03-06T18:20:57.1218|INFO|StarDetection.cs|Detect|234|Average HFR: 19.039940801378577, HFR \u00b3: 0, Detected Stars 1, Sensitivity Normal, ResizeFactor: 0.25
"""

result = parse_nina_log(test_log)

print(f'autofocus_runs: {len(result["autofocus_runs"])}')
print(f'timeline_phases: {len(result["timeline_phases"])}')

if result["autofocus_runs"]:
    af = result["autofocus_runs"][0]
    print(f'AF Run details:')
    print(f'  Run index: {af["run_index"]}')
    print(f'  Status: {af["status"]}')
    print(f'  Start time: {af["start_time"]}')
    print(f'  Temperature: {af.get("temperature")}')
    print(f'  Final position: {af.get("final_position")}')
    print(f'  Best HFR: {af.get("best_hfr")}')
    print(f'  Best stars: {af.get("best_stars")}')
    print(f'  Steps: {len(af["steps"])}')
    if af["steps"]:
        print(f'First 3 steps:')
        for step in af["steps"][:3]:
            print(f'  Position {step["position"]}: HFR={step.get("hfr")}, stars={step.get("star_count")}')
else:
    print('No autofocus runs found!')
