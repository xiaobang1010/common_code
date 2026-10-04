/**
 * show_widget 载荷解析：合并「工具入参」与「工具结果」两路来源。
 * 流式期间入参是未闭合的 JSON 字符串，需要部分解析；
 * 结果文本里若含 visualizer_show_widget_result 则以结果为准。
 */

export interface WidgetPayload {
	title?: string;
	widget_code?: string;
	loading_messages: string[];
}

function parseStringArray(value: unknown): string[] {
	if (Array.isArray(value)) return value.map((item) => typeof item === "string" ? item : String(item)).map((item) => item.trim()).filter(Boolean);
	if (typeof value === "string") {
		const trimmed = value.trim();
		if (!trimmed) return [];
		if (trimmed.startsWith("[")) try {
			const parsed = JSON.parse(trimmed);
			if (Array.isArray(parsed)) return parsed.map((item) => typeof item === "string" ? item : String(item)).map((item) => item.trim()).filter(Boolean);
		} catch {}
		return trimmed.replace(/^\[/, "").replace(/\]$/, "").split(/[\n,，]/g).map((item) => item.trim()).map((item) => item.replace(/^["'“”‘’]+/, "").replace(/["'“”‘’]+$/, "").trim()).filter(Boolean);
	}
	return [];
}

function parseJsonSafe(value: unknown): Record<string, unknown> | undefined {
	if (typeof value !== "string" || !value.trim()) return;
	try {
		const parsed = JSON.parse(value);
		if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
	} catch {}
}

/** 从未闭合 JSON 字符串中尽量提取 title/widget_code（流式中途帧） */
function parsePartialJson(content: string): Record<string, unknown> | null {
	const trimmed = content.trim();
	if (!trimmed.startsWith("{")) return null;
	const result: Record<string, unknown> = {};
	const titleMatch = trimmed.match(/"title"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"/);
	if (titleMatch) try {
		result.title = JSON.parse(`"${titleMatch[1]}"`);
	} catch {
		result.title = titleMatch[1];
	}
	const widgetCodeStart = trimmed.indexOf("\"widget_code\"");
	if (widgetCodeStart !== -1) {
		const colonPos = trimmed.indexOf(":", widgetCodeStart);
		if (colonPos !== -1) {
			const valueStart = trimmed.indexOf("\"", colonPos);
			if (valueStart !== -1) {
				let valueEnd = -1;
				let escape = false;
				for (let i = valueStart + 1; i < trimmed.length; i++) {
					if (escape) {
						escape = false;
						continue;
					}
					if (trimmed[i] === "\\") {
						escape = true;
						continue;
					}
					if (trimmed[i] === "\"") {
						valueEnd = i;
						break;
					}
				}
				if (valueEnd !== -1) try {
					result.widget_code = JSON.parse(trimmed.substring(valueStart, valueEnd + 1));
				} catch {
					result.widget_code = trimmed.substring(valueStart + 1, valueEnd);
				}
				else result.widget_code = trimmed.substring(valueStart + 1).replace(/\\n/g, "\n").replace(/\\t/g, "\t").replace(/\\"/g, "\"").replace(/\\\\/g, "\\");
			}
		}
	}
	return Object.keys(result).length > 0 ? result : null;
}

function pickSource(source: Record<string, any>): WidgetPayload {
	return {
		title: typeof source.title === "string" ? source.title : void 0,
		widget_code: typeof source.widget_code === "string" && source.widget_code !== "" ? source.widget_code : typeof source.widgetCode === "string" && source.widgetCode !== "" ? source.widgetCode : void 0,
		loading_messages: parseStringArray(source.loading_messages ?? source.loadingMessages),
	};
}

function extractShowWidgetResult(rawResult: unknown): Record<string, any> | undefined {
	const stack: unknown[] = [rawResult];
	while (stack.length > 0) {
		const current = stack.pop();
		if (!current || typeof current !== "object") continue;
		if (Array.isArray(current)) {
			stack.push(...current);
			continue;
		}
		const obj = current as Record<string, any>;
		if (obj.type === "visualizer_show_widget_result") return obj;
		if (typeof obj.result === "string") {
			const parsed = parseJsonSafe(obj.result);
			if (parsed) stack.push(parsed);
		}
		Object.values(obj).forEach((value) => {
			if (value && typeof value === "object") stack.push(value);
		});
	}
}

/**
 * 解析一个 show_widget 步骤的载荷。
 * @param args 工具入参原始 JSON 字符串（流式期间可能未闭合）
 * @param result 工具结果文本（可能为空或截断展示串）
 */
export function parseWidgetPayload(args?: string, result?: string): WidgetPayload {
	const argsObj = (args && (parseJsonSafe(args) || parsePartialJson(args))) || {};
	const argsPayload = pickSource(argsObj as Record<string, any>);
	const direct = extractShowWidgetResult(result);
	if (!direct) return argsPayload;
	const resultMessages = parseStringArray(direct.loading_messages ?? direct.loadingMessages);
	return {
		title: typeof direct.title === "string" && direct.title ? direct.title : argsPayload.title,
		widget_code: typeof direct.widget_code === "string" && direct.widget_code !== "" ? direct.widget_code : typeof direct.widgetCode === "string" && direct.widgetCode !== "" ? direct.widgetCode : argsPayload.widget_code,
		loading_messages: resultMessages.length > 0 ? resultMessages : argsPayload.loading_messages,
	};
}
