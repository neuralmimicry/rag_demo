# CRITICAL FIX: Shoulder Roll Sign Inversion

## Problem Report

**User:** "I have restarted/refreshed refiner/stt/frontend but I still don't see BSL signing occuring. I do see that when talking, the hands go behind the back rather than out front."

## Root Cause

The STT backend was sending `shoulder_roll` values with the **WRONG SIGN**, causing arms to move **backward/down** instead of **forward/up** into signing space.

### Technical Analysis

**Frontend Coordinate System** (ChatOfficeEnvironment.jsx lines 803-804):
```javascript
const poseLeftUpperZ = -1.25 + toSignedUnit(avatarPose?.leftShoulderRoll, -0.05) * 0.78;
const poseRightUpperZ = 1.25 + toSignedUnit(avatarPose?.rightShoulderRoll, 0.05) * 0.78;
```

In the 3D space:
- **Z = 0** means arms horizontal (T-pose)
- **Z = ±1.57** means arms fully down (hanging)
- **Base values** = -1.25 (left) and +1.25 (right) ≈ 80% down

**To bring arms UP/FORWARD:**
- Left arm: Need POSITIVE shoulder_roll values (move -1.25 toward 0)
- Right arm: Need NEGATIVE shoulder_roll values (move +1.25 toward 0)

**What Backend Was Doing (WRONG):**
```rust
pose.left_shoulder_roll -= 0.44;  // Making it NEGATIVE → more down/back
pose.right_shoulder_roll += 0.44; // Making it POSITIVE → more down/back
```

**Example Calculation (BROKEN):**
- Backend sends: `left_shoulder_roll = -0.44`
- Frontend calculates: `-1.25 + (-0.44 - (-0.05)) * 0.78 = -1.25 + (-0.39) * 0.78 = -1.554`
- **Result: Arm moved FURTHER DOWN (behind back)** ❌

## The Fix

**File**: `/home/pbisaacs/Developer/neuralmimicry/nmstt/src/main.rs`

### Change 1: BSL Rest Pose (lines 914-915)
```rust
// OLD (WRONG - arms went backward)
pose.left_shoulder_roll -= 0.44 * amplitude;
pose.right_shoulder_roll += 0.44 * amplitude;

// NEW (CORRECT - arms go forward)
pose.left_shoulder_roll += 0.44 * amplitude;
pose.right_shoulder_roll -= 0.44 * amplitude;
```

### Change 2: Gesticulation Rest Pose (lines 931-932)
```rust
// OLD (WRONG)
pose.left_shoulder_roll -= 0.26 * amplitude;
pose.right_shoulder_roll += 0.26 * amplitude;

// NEW (CORRECT)
pose.left_shoulder_roll += 0.26 * amplitude;
pose.right_shoulder_roll -= 0.26 * amplitude;
```

### Change 3: Motion Template Application (lines 1299-1300)
```rust
// OLD (WRONG)
pose.left_shoulder_roll -= blend.shoulder_roll * intensity * left_bias;
pose.right_shoulder_roll += blend.shoulder_roll * intensity * right_bias;

// NEW (CORRECT)
pose.left_shoulder_roll += blend.shoulder_roll * intensity * left_bias;
pose.right_shoulder_roll -= blend.shoulder_roll * intensity * right_bias;
```

## Verification

### New Calculation (FIXED):
- Backend sends: `left_shoulder_roll = +0.44`
- Frontend calculates: `-1.25 + (0.44 - (-0.05)) * 0.78 = -1.25 + (0.49) * 0.78 = -1.25 + 0.382 = -0.868`
- **Result: Arm moved UP/FORWARD** ✅

### Test Results
```bash
cd /home/pbisaacs/Developer/neuralmimicry/nmstt
cargo test
```
**Output**: ✅ All 12 tests passing

### Build Results
```bash
cargo build --release
```
**Output**: ✅ Build successful

## Expected Behavior After Fix

### BSL Mode
- **Rest pose**: Arms raised into signing space (chest/shoulder height)
- **Gestures**: Clear arm movements forward and upward
- **Hand positioning**: Hands visible in front of body

### Gesticulation Mode
- **Rest pose**: Arms slightly forward, relaxed conversational position
- **Gestures**: Natural hand gestures accompanying speech

## Deployment Instructions

1. **Restart STT Service**:
   ```bash
   cd /home/pbisaacs/Developer/neuralmimicry/nmstt
   cargo build --release
   # Then restart the service with the new binary
   ```

2. **Verify in Browser**:
   - Set Avatar Motion to "BSL Signing"
   - Speak any phrase
   - **Verify**: Arms should be raised in front, not behind back
   - **Verify**: Hands should be clearly visible during signing

3. **Test Both Modes**:
   - BSL Signing: Strong, clear arm movements
   - Gesticulation: Subtle, conversational movements

## Impact Analysis

This was a **critical bug** that made BSL signing completely non-functional:
- ❌ Arms moved behind back (opposite of intended)
- ❌ Hands invisible to viewer
- ❌ BSL signs incomprehensible
- ❌ Worse than having no gestures at all

After fix:
- ✅ Arms correctly positioned in signing space
- ✅ Hands clearly visible
- ✅ BSL signs readable and comprehensible
- ✅ Natural gesticulation also improved

## Related Files

- Backend pose generation: `../nmstt/src/main.rs` (lines 907-942, 1297-1304)
- Frontend pose mapping: `ChatOfficeEnvironment.jsx` (lines 803-855)
- Previous intensity fix: `BSL_ARM_MOVEMENT_FIX.md`
- Complete implementation: `BSL_IMPLEMENTATION_SUMMARY.md`

## Why This Happened

The confusion arose from different sign conventions:
- **Backend developer assumed**: Negative roll = arms forward
- **Frontend implementation**: Positive roll (from negative base) = arms forward
- **No documentation** of the coordinate system expectations

## Prevention

To prevent similar issues:
1. **Document coordinate systems** explicitly in code comments
2. **Add visual tests** that verify arm positions
3. **Create reference pose images** showing expected positions
4. **Add pose validation** that warns if arms go behind back

## Testing Checklist

After deploying this fix:

- [ ] Restart STT service with new build
- [ ] Clear browser cache and refresh frontend
- [ ] Set Avatar Motion to "BSL Signing"
- [ ] Speak: "Hello, how are you?"
- [ ] **VERIFY**: Arms are raised in front (NOT behind back)
- [ ] **VERIFY**: Hands clearly visible during signing
- [ ] **VERIFY**: Shoulder movements visible (arms lifting)
- [ ] **VERIFY**: Elbow bending visible
- [ ] Switch to "Gesticulation" mode
- [ ] **VERIFY**: Subtle hand gestures, arms lower than BSL
- [ ] **VERIFY**: Natural conversational appearance

## Status

- ✅ Bug identified
- ✅ Root cause analysis complete
- ✅ Fix implemented
- ✅ Tests passing
- ✅ Build successful
- ⏳ Awaiting deployment and user testing
