/**
 * 对话流中的可视化步骤视图：
 * - WidgetStep：show_widget 的 chromeless 内联渲染（流式期间增量刷新，完成后定稿）
 * - GuidelinesStep：widget_guidelines 的紧凑一行（不剧透规范内容，可展开查看）
 */
import { memo, useEffect, useMemo, useState } from 'react';
import { useChatStore } from '../../stores/useChatStore';
import { parseWidgetPayload } from '../../utils/widgetPayload';
import { WidgetRenderer, type WidgetHostConfig } from './WidgetRenderer';
import type { TimelineItem } from '../../stores/useChatStore';

const DEFAULT_LOADING_MESSAGES = ["正在准备渲染"];

function isRunningStatus(step: TimelineItem): boolean {
	return !!step.isRunning;
}

// 从失败结果文本中提取错误原因（工具校验失败时 is_error 文案）
function extractStepError(step: TimelineItem): string | undefined {
	if (!step.result) return;
	const parsed = (() => {
		try {
			return JSON.parse(step.result);
		} catch {
			return null;
		}
	})();
	if (parsed && parsed.type === "visualizer_show_widget_result" && parsed.success === false) {
		return typeof parsed.message === "string" ? parsed.message : undefined;
	}
	return undefined;
}

const Spinner = () => (
	<span className="inline-loading-line__spinner" aria-hidden="true" />
);

const InlineLoadingLine = ({ message }: { message?: string }) => {
	if (!message) return null;
	return (
		<div className="inline-loading-line" role="status" aria-live="polite">
			<span className="inline-loading-line__icon">
				<Spinner />
			</span>
			<span className="inline-loading-line__text">{message}</span>
		</div>
	);
};

export const WidgetStep = memo(function WidgetStep({ step }: { step: TimelineItem }) {
	const payload = useMemo(() => parseWidgetPayload(step.args, step.result), [step.args, step.result]);
	const isRunning = isRunningStatus(step);
	const toolError = extractStepError(step);
	const loadingMessages = payload.loading_messages && payload.loading_messages.length > 0 ? payload.loading_messages : DEFAULT_LOADING_MESSAGES;
	const [loadingIndex, setLoadingIndex] = useState(0);
	useEffect(() => {
		if (!isRunning || loadingMessages.length <= 1) return;
		const timer = window.setInterval(() => {
			setLoadingIndex((prev) => (prev + 1) % loadingMessages.length);
		}, 1400);
		return () => window.clearInterval(timer);
	}, [isRunning, loadingMessages]);

	const hostConfig: WidgetHostConfig = useMemo(() => ({
		// 沙箱内 sendPrompt：作为用户消息发回当前会话
		onSendMessage: (text) => {
			void useChatStore.getState().sendMessage(text);
		},
		onOpenLink: (href) => {
			window.open(href, '_blank', 'noopener,noreferrer');
		},
	}), []);

	if (isRunning) {
		if (payload.widget_code) return (
			<WidgetRenderer
				key={step.id}
				widgetCode={payload.widget_code}
				title={payload.title}
				toolName="show_widget"
				isStreaming
				chromeless
				showOverlay={false}
				loadingLabel={loadingMessages[loadingIndex]}
				hostConfig={hostConfig}
			/>
		);
		return <InlineLoadingLine message={loadingMessages[loadingIndex] || DEFAULT_LOADING_MESSAGES[0]} />;
	}
	if (toolError) return <div className="cc-widget-step-error">{toolError}</div>;
	if (!payload.widget_code) return <div className="cc-widget-step-error">Widget content is missing.</div>;
	return (
		<WidgetRenderer
			key={step.id}
			widgetCode={payload.widget_code}
			title={payload.title}
			toolName="show_widget"
			isStreaming={false}
			chromeless
			showOverlay={false}
			loadingLabel={loadingMessages[loadingIndex]}
			hostConfig={hostConfig}
		/>
	);
});

export const GuidelinesStep = memo(function GuidelinesStep({ step }: { step: TimelineItem }) {
	const [expanded, setExpanded] = useState(false);
	const isRunning = isRunningStatus(step);
	const isCompleted = !isRunning && !!step.result;
	const resultStr = step.result || "";
	const canExpand = isCompleted && !!resultStr;
	return (
		<div className="visualizer-read-me-compact">
			<div
				className={`visualizer-read-me-compact__header${canExpand ? " expandable" : ""}`}
				onClick={() => {
					if (!canExpand) return;
					const sel = window.getSelection();
					if (sel) sel.removeAllRanges();
					setExpanded((prev) => !prev);
				}}
			>
				<span className={`visualizer-read-me-compact__action${isRunning ? " cc-shining-text" : ""}`}>
					加载可视化设计规范
				</span>
				<span className="visualizer-read-me-compact__sub-text">
					{isRunning ? "加载中…" : "已加载"}
				</span>
				{canExpand && (
					<span className={`visualizer-read-me-compact__arrow${expanded ? " expanded" : ""}`}>▾</span>
				)}
			</div>
			{expanded && (
				<div className="visualizer-read-me-compact__content">
					<div className="visualizer-read-me-compact__content-body">
						<pre>{resultStr}</pre>
					</div>
				</div>
			)}
		</div>
	);
});
