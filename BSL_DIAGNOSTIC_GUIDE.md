# BSL Arm Movement Diagnostic Guide

## Current Status

**Issue**: Arms still moving behind the back during speech/signing instead of forward BSL movements

**Changes Applied**:
1. ✅ Fixed shoulder_roll sign inversion in backend
2. ✅ Increased motion intensity scaling in frontend
3. ✅ Added debug logging to both backend and frontend

## How to Diagnose

### Step 1: Restart STT Service

```bash
cd /home/pbisaacs/Developer/neuralmimicry/rag_demo/stt_rust

# Kill existing STT service
pkill -f refiner-stt

# Start new service (watch console output)
./target/release/refiner-stt --bind 127.0.0.1:8002
```

### Step 2: Restart Frontend

```bash
cd /home/pbisaacs/Developer/neuralmimicry.ai-website
npm run dev
```

### Step 3: Test and Check Logs

1. Open browser to your website
2. Open browser DevTools Console (F12)
3. Set "Avatar Motion" to "BSL Signing"
4. Speak a test phrase: "Hello, how are you?"
5. **Check both logs simultaneously**

## Expected Log Output

### Backend Console (STT Service)

Should see after transcription:
```
[DEBUG] First keyframe pose values for gesture_mode=bsl
  left_shoulder_pitch: 0.220
  right_shoulder_pitch: 0.220
  left_shoulder_roll: 0.440
  right_shoulder_roll: -0.440
  left_elbow: 0.420
  right_elbow: 0.420
```

**Key Validation**:
- ✅ `left_shoulder_roll` should be POSITIVE (~0.44)
- ✅ `right_shoulder_roll` should be NEGATIVE (~-0.44)
- ✅ Both elbows should be POSITIVE (~0.42)

❌ If shoulders are wrong sign, backend not restarted with new code
❌ If no log appears, gestures not being generated

### Frontend Console (Browser)

Should see:
```javascript
[BSL DEBUG] playAvatarMotionFromPayload called: {
  hasMotionClip: true,
  keyframeCount: 25,  // or similar number
  resolvedMotionMode: "bsl",
  firstFrameShoulderRoll: {
    left: 0.44,
    right: -0.44
  }
}
```

**Key Validation**:
- ✅ `hasMotionClip` must be `true`
- ✅ `keyframeCount` should be > 0
- ✅ `resolvedMotionMode` should be `"bsl"`
- ✅ Shoulder roll values should match backend

❌ If `hasMotionClip: false`, motion data not sent/received
❌ If wrong mode, mode parameter not passed correctly
❌ If shoulder values are zeros, pose data corrupt

## Troubleshooting Scenarios

### Scenario 1: No Backend Log

**Symptom**: No `[DEBUG] First keyframe pose values` in STT console

**Possible Causes**:
- Gestures disabled: Check `REFINER_STT_GESTURE_ENABLED` env var
- Request not reaching STT: Check network/proxy
- Wrong transcription endpoint: Should be `/transcribe`

**Fix**:
```bash
export REFINER_STT_GESTURE_ENABLED=true
# Restart STT service
```

### Scenario 2: Backend Shows Wrong Values

**Symptom**: Backend log shows negative left_shoulder_roll or positive right_shoulder_roll

**Cause**: Old binary still running

**Fix**:
```bash
pkill -f refiner-stt
cargo build --release
./target/release/refiner-stt --bind 127.0.0.1:8002
```

### Scenario 3: Frontend Shows hasMotionClip: false

**Symptom**: `hasMotionClip: false` in browser console

**Possible Causes**:
- Backend not sending avatar_motion in response
- Network issue/CORS
- Wrong API endpoint

**Diagnosis**:
1. Check Network tab in DevTools
2. Find the `/transcribe` request
3. Look at response JSON
4. Verify `avatar_motion` field exists with `keyframes` array

**Fix**:
- If missing: Backend not generating gestures
- If present but not extracted: Frontend extraction logic issue

### Scenario 4: Motion Clip Present But Arms Wrong

**Symptom**: All logs look correct but avatar arms still behind back

**Possible Causes**:
1. **Mode is not 'signing'**: Avatar uses lower pose influence when mode='idle'
2. **clampToHome overriding**: ChatAvatarPanel has clampToHome=true
3. **Pose blend factor too low**: Motion being blended out

**Diagnosis**:
```javascript
// Add to ChatOfficeEnvironment.jsx around line 784
console.log('[POSE DEBUG]', {
  mode,
  isBsl,
  poseInfluence,
  armPoseInfluence,
  avatarPose,
  poseLeftUpperZ,
  poseRightUpperZ,
});
```

**Expected Values**:
- `mode`: Should be 'speaking' or 'signing' when voice active
- `poseInfluence`: Should be 0.74+ for BSL
- `armPoseInfluence`: Should match poseInfluence (not 0)
- `poseLeftUpperZ`: Should be around -0.9 (not -1.5)
- `poseRightUpperZ`: Should be around +0.9 (not +1.5)

### Scenario 5: Arms Move Slightly But Not Enough

**Symptom**: Arms move a little but not to full BSL signing position

**Possible Causes**:
1. **Intensity too low**: Check SOFTENED_MOTION_SCALES
2. **Pose influence too low**: Mode not correctly detected
3. **Amplitude calculation wrong**: Backend amplitude values

**Current Intensity Settings** (Should be):
```javascript
// AIChatWidget.jsx line 465-467
bsl: {
  office: { intensity: 0.95, duration: 1.08, blend: 0.22 },
  chat: { intensity: 0.88, duration: 1.12, blend: 0.20 },
},
```

**Verify**:
```javascript
// Add after line 779 in playAvatarMotionFromPayload
console.log('[SOFTENING]', {
  originalDuration: motionClip.durationMs,
  softenedDuration: softenedClip.durationMs,
  blendStrength: softenedClip.blendStrength,
  firstPoseAfterSoften: softenedClip.keyframes[0].pose.leftShoulderRoll,
});
```

## Known Working Configuration

**Backend Pose Values (BSL rest):**
```rust
left_shoulder_pitch: +0.22
right_shoulder_pitch: +0.22
left_shoulder_roll: +0.44  // ← Must be POSITIVE
right_shoulder_roll: -0.44  // ← Must be NEGATIVE
left_elbow: 0.42
right_elbow: 0.42
```

**Frontend Calculation:**
```javascript
// With backend values above:
poseLeftUpperZ = -1.25 + (0.44 * 0.78) = -0.907  // Arms UP
poseRightUpperZ = 1.25 + (-0.44 * 0.78) = 0.907  // Arms UP
```

**Frontend Settings:**
- BSL intensity: 0.88 (chat) or 0.95 (office)
- Pose influence: 0.74 (speaking + BSL) or 0.92 (signing + BSL)
- Mode must be: 'speaking' or 'signing' (not 'idle')

## Quick Test Script

Create this test to verify motion data:

```javascript
// Run in browser console after speaking
const lastMessage = messages[messages.length - 1];
console.log('Last message:', {
  text: lastMessage?.text,
  hasBslText: !!lastMessage?.bslText,
  bslText: lastMessage?.bslText,
});

// Check current avatar state
console.log('Avatar state:', {
  isAvatarMotionPlaying,
  activeMotionMode,
  avatarPose: {
    leftShoulderRoll: avatarPose?.leftShoulderRoll,
    rightShoulderRoll: avatarPose?.rightShoulderRoll,
    leftElbow: avatarPose?.leftElbow,
  },
});
```

## Next Steps If Still Failing

If after all diagnostics arms still go behind back:

1. **Capture full request/response**:
   ```bash
   # In STT service, add verbose logging
   export RUST_LOG=debug
   ./target/release/refiner-stt --bind 127.0.0.1:8002
   ```

2. **Check raw motion data**:
   - Open Network tab
   - Find `/transcribe` response
   - Copy `avatar_motion.keyframes[0].pose` JSON
   - Verify shoulder_roll values directly

3. **Test with static pose**:
   ```javascript
   // In browser console, force a test pose
   setAvatarPose({
     leftShoulderPitch: 0.2,
     rightShoulderPitch: 0.2,
     leftShoulderRoll: 0.5,   // Should bring arms forward
     rightShoulderRoll: -0.5,  // Should bring arms forward
     leftElbow: 0.4,
     rightElbow: 0.4,
   });
   ```

   If this works: motion playback issue
   If this fails: pose interpretation issue in ChatOfficeEnvironment

## Files to Check

**Backend**:
- `stt_rust/src/main.rs` lines 914-915, 931-932, 1299-1300

**Frontend**:
- `src/components/AIChatWidget.jsx` lines 464-473, 757-806
- `src/components/ChatOfficeEnvironment.jsx` lines 803-855
- `src/components/ChatAvatarPanel.jsx` line 65 (clampToHome)

## Contact/Escalation

If all diagnostics pass but arms still wrong:
1. Provide screenshots of BOTH console logs
2. Provide Network tab screenshot of `/transcribe` response
3. Provide video showing arm position during signing
4. Note: Which mode (chat vs office) showing issue
