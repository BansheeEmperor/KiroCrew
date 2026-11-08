/**
 * The App Kit seam level this dashboard provides. Mirrors
 * `src/kiro_crew/apps/app_sdk_level.py`; `test/test_app_sdk_level_mirror.py` pins
 * the two equal. An app bundle reads it (namespace import, feature-detected) to
 * tell an older host from one that has the seams it was built against. Level 4
 * adds the Bot hooks (`useBots`, `useBot`, `useWakeBot`).
 */
export const APP_SDK_LEVEL = 4
