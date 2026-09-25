/**
 * Widget 高度守卫：归一化 iframe 上报高度，并拦截「微小增长连发」造成的抖动。
 */

export const WIDGET_MAX_HEIGHT = 2000;
const MAX_TINY_GROWTH_DELTA = 2;
const TINY_GROWTH_WINDOW_MS = 300;
const MAX_TINY_GROWTH_STREAK = 6;

export interface WidgetHeightGuardState {
	lastAcceptedAt: number;
	tinyGrowthStreak: number;
	rapidGrowthStreak: number;
}

export function normalizeWidgetHeight(rawHeight: unknown, defaultHeight: number, maxHeight: number): number {
	const numericHeight = typeof rawHeight === "number" ? rawHeight : Number(rawHeight);
	return Math.min(Math.max(Number.isFinite(numericHeight) ? numericHeight : defaultHeight, 20), maxHeight);
}

export function shouldApplyWidgetHeight(nextHeight: number, prevHeight: number, now: number, guardState: WidgetHeightGuardState): boolean {
	if (nextHeight === prevHeight) return false;
	const delta = nextHeight - prevHeight;
	const elapsed = now - guardState.lastAcceptedAt;
	if (delta < 0) {
		guardState.tinyGrowthStreak = 0;
		guardState.rapidGrowthStreak = 0;
		return true;
	}
	guardState.rapidGrowthStreak = 0;
	if (delta <= MAX_TINY_GROWTH_DELTA && elapsed >= 0 && elapsed < TINY_GROWTH_WINDOW_MS) guardState.tinyGrowthStreak += 1;
	else guardState.tinyGrowthStreak = 0;
	if (guardState.tinyGrowthStreak >= MAX_TINY_GROWTH_STREAK) return false;
	return true;
}

export function markWidgetHeightAccepted(now: number, guardState: WidgetHeightGuardState): void {
	guardState.lastAcceptedAt = now;
}
