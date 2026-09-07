# patch validation workflow

When validating a newly added patch:

1. Confirm the exact bug mechanism from current logs/data.
2. Check whether the patch-specific flags/logs appeared.
3. Compare before vs after using existing diagnostic tools.
4. Determine:
   - did the patch activate
   - did it improve conversion
   - did it create side effects
5. If the patch worked, identify the next bottleneck.
6. If the patch did not work, identify the precise stall point.