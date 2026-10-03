// 页面注入运行时库：以源码字符串形式经 CDP 注入 guest 页面，挂在 window.__inappBrowser。
// 控制服务的 playwright 命令统一经 dispatch(action) 调用；返回 {value} 表示取值类结果，
// 返回 {input:...} 表示需要主进程补一段真实输入（坐标点击/按键），其余抛错由主进程映射为协议错误。
// 本文件在页面上下文执行，不得使用 Node/Electron API。

const PAGE_RUNTIME_SOURCE = `(function () {
  if (window.__inappBrowser) return;

  var DEFAULT_TIMEOUT_MS = 3000;

  // ---------- 基础工具 ----------
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function trimText(s, max) {
    var t = String(s == null ? '' : s).replace(/\\s+/g, ' ').trim();
    if (max && t.length > max) t = t.slice(0, max - 1) + '…';
    return t;
  }

  function isVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    var style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    var rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  // 标签/类型 → ARIA 隐式角色（够用子集：交互元素与结构地标）
  function computeRole(el) {
    var explicit = el.getAttribute && el.getAttribute('role');
    if (explicit) return explicit;
    var tag = el.tagName.toLowerCase();
    var type = (el.getAttribute && el.getAttribute('type') || '').toLowerCase();
    switch (tag) {
      case 'a': return el.hasAttribute('href') ? 'link' : null;
      case 'button': return 'button';
      case 'nav': return 'navigation';
      case 'main': return 'main';
      case 'header': return 'banner';
      case 'footer': return 'contentinfo';
      case 'form': return 'form';
      case 'article': return 'article';
      case 'section': return (el.hasAttribute('aria-label') || el.hasAttribute('aria-labelledby')) ? 'region' : null;
      case 'aside': return 'complementary';
      case 'dialog': return 'dialog';
      case 'img': return el.getAttribute('alt') === '' ? 'presentation' : 'img';
      case 'h1': case 'h2': case 'h3': case 'h4': case 'h5': case 'h6': return 'heading';
      case 'ul': case 'ol': return 'list';
      case 'li': return 'listitem';
      case 'table': return 'table';
      case 'tr': return 'row';
      case 'td': return 'cell';
      case 'th': return 'columnheader';
      case 'select': return type === 'multiple' ? 'listbox' : 'combobox';
      case 'textarea': return 'textbox';
      case 'input':
        if (['button', 'submit', 'reset', 'image'].indexOf(type) >= 0) return 'button';
        if (type === 'checkbox') return 'checkbox';
        if (type === 'radio') return 'radio';
        if (type === 'range') return 'slider';
        if (type === 'number') return 'spinbutton';
        if (type === 'search') return 'searchbox';
        if (['email', 'tel', 'url', 'text', 'password', ''].indexOf(type) >= 0) return 'textbox';
        return null;
      default: return null;
    }
  }

  // 可访问名：aria-label > aria-labelledby > 控件专属来源 > 文本内容
  function accessibleName(el) {
    var label = el.getAttribute && el.getAttribute('aria-label');
    if (label) return trimText(label, 100);
    var by = el.getAttribute && el.getAttribute('aria-labelledby');
    if (by) {
      var parts = [];
      by.split(/\\s+/).forEach(function (id) {
        var ref = document.getElementById(id);
        if (ref) parts.push(trimText(ref.textContent, 60));
      });
      if (parts.length) return parts.join(' ').slice(0, 100);
    }
    var tag = el.tagName.toLowerCase();
    if (tag === 'img') return trimText(el.getAttribute('alt') || el.getAttribute('title'), 100);
    if (tag === 'input' || tag === 'textarea') {
      if (el.labels && el.labels.length) return trimText(el.labels[0].textContent, 100);
      var ph = el.getAttribute('placeholder');
      if (ph) return trimText(ph, 100);
      var val = el.value;
      if (val && ['button', 'submit', 'reset'].indexOf((el.type || '').toLowerCase()) >= 0) return trimText(val, 100);
      return '';
    }
    var role = computeRole(el);
    if (['heading', 'button', 'link', 'cell', 'columnheader', 'row', 'listitem', 'option', 'tab', 'menuitem', 'checkbox', 'radio'].indexOf(role) >= 0) {
      return trimText(el.textContent, 100);
    }
    var title = el.getAttribute && el.getAttribute('title');
    return title ? trimText(title, 100) : '';
  }

  function stateMarks(el, role) {
    var marks = [];
    if (el.disabled === true) marks.push('disabled');
    if (el.getAttribute && (el.getAttribute('aria-disabled') === 'true')) marks.push('disabled');
    if (role === 'checkbox' || role === 'radio') {
      if (el.checked) marks.push('checked');
    } else if (el.getAttribute && el.getAttribute('aria-checked') === 'true') {
      marks.push('checked');
    }
    if (el.getAttribute && el.getAttribute('aria-expanded') === 'true') marks.push('expanded');
    if (el.getAttribute && el.getAttribute('aria-selected') === 'true') marks.push('selected');
    if (el.getAttribute && el.getAttribute('required') !== null) marks.push('required');
    var tag = el.tagName.toLowerCase();
    if (/^h[1-6]$/.test(tag)) marks.push('level=' + tag.slice(1));
    if (document.activeElement === el) marks.push('focused');
    return marks;
  }

  var INTERACTIVE_ROLES = ['link', 'button', 'textbox', 'searchbox', 'checkbox', 'radio', 'combobox', 'listbox', 'slider', 'spinbutton', 'menuitem', 'tab', 'option', 'switch'];

  function isInteresting(el) {
    var role = computeRole(el);
    if (role && (INTERACTIVE_ROLES.indexOf(role) >= 0 || ['heading', 'img', 'navigation', 'main', 'form', 'dialog', 'list', 'table'].indexOf(role) >= 0)) return true;
    if (el.tabIndex >= 0) return true;
    if (el.onclick || el.onchange || el.oninput) return true;
    return false;
  }

  // ---------- domSnapshot：紧凑 ARIA 树 ----------
  function snapshotWalk(node, depth, lines) {
    var children = node.children || [];
    var indent = '  '.repeat(depth);
    // 纯文本叶子节点聚合输出，避免长文逐字成行
    var textOnly = node.textContent && node.children.length === 0;
    for (var i = 0; i < children.length; i++) {
      var el = children[i];
      if (!el.getBoundingClientRect) continue;
      if (['script', 'style', 'noscript', 'svg', 'path'].indexOf(el.tagName.toLowerCase()) >= 0) continue;
      if (!isVisible(el)) continue;
      var role = computeRole(el);
      var interesting = isInteresting(el);
      var name = interesting || role ? accessibleName(el) : '';
      if (interesting || (role && name)) {
        var marks = stateMarks(el, role);
        var line = indent + '- ' + (role || el.tagName.toLowerCase());
        if (name) line += ' "' + name + '"';
        if (role === 'link' && el.href) line += ' [url=' + trimText(el.href, 80) + ']';
        if (marks.length) line += ' [' + marks.join(' ') + ']';
        lines.push(line);
        snapshotWalk(el, depth + 1, lines);
      } else if (textOnly) {
        var t = trimText(el.textContent, 120);
        if (t) lines.push(indent + '- text: "' + t + '"');
      } else {
        snapshotWalk(el, depth + 1, lines);
      }
    }
    // open shadow DOM 一并展开
    if (node.host && node.host.shadowRoot) snapshotWalk(node.host.shadowRoot, depth, lines);
  }

  function domSnapshot() {
    var lines = ['- RootWebArea "' + trimText(document.title, 80) + '"'];
    snapshotWalk(document.body, 1, lines);
    // 同源 iframe 内容附加在末尾
    var frames = document.querySelectorAll('iframe');
    for (var f = 0; f < frames.length; f++) {
      try {
        var doc = frames[f].contentDocument;
        if (doc && doc.body) {
          var sub = ['- Frame "' + trimText(frames[f].src || '', 60) + '"'];
          snapshotWalk(doc.body, 1, sub);
          lines = lines.concat(sub);
        }
      } catch (e) { /* 跨域 iframe 跳过 */ }
    }
    return lines.join('\\n');
  }

  // ---------- locator 解析（严格模式：多匹配即报错） ----------
  async function resolveLocator(selector, timeoutMs, options) {
    var deadline = Date.now() + (timeoutMs || DEFAULT_TIMEOUT_MS);
    var opts = options || {};
    var lastCount = -1;
    while (Date.now() < deadline) {
      var els = document.querySelectorAll(selector);
      lastCount = els.length;
      if (els.length > 1 && opts.strict !== false) {
        throw new Error('strict mode violation: "' + selector + '" matched ' + els.length + ' elements');
      }
      if (els.length >= 1 && (!opts.visible || isVisible(els[0]))) return els;
      await sleep(100);
    }
    if (lastCount === 0) throw new Error('locator not found: "' + selector + '"');
    throw new Error('locator not visible within timeout: "' + selector + '"');
  }

  function firstVisible(els) {
    for (var i = 0; i < els.length; i++) if (isVisible(els[i])) return els[i];
    return els[0];
  }

  // 输入框赋值：走原生 setter 以兼容 React/Vue 受控组件，再派发 input/change
  function setNativeValue(el, value) {
    var proto = el.tagName.toLowerCase() === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, 'value');
    if (setter && setter.set) setter.set.call(el, value); else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function centerOf(el) {
    el.scrollIntoView({ block: 'center', inline: 'center' });
    var rect = el.getBoundingClientRect();
    return { x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2) };
  }

  // ---------- dispatch：playwright action 统一入口 ----------
  async function dispatch(action) {
    var name = action.name;

    if (name === 'domSnapshot') return { value: domSnapshot() };

    if (name === 'evaluate') {
      var fn = new Function('return (' + action.expression + ')')();
      var argVal = typeof action.arg === 'string' ? action.arg : action.arg;
      return { value: await fn(argVal) };
    }

    if (name === 'elementInfo') {
      var hit = document.elementFromPoint(action.x, action.y);
      if (!hit) return { value: null };
      var rect = hit.getBoundingClientRect();
      return {
        value: {
          tag: hit.tagName.toLowerCase(),
          role: computeRole(hit) || undefined,
          name: accessibleName(hit) || undefined,
          text: trimText(hit.textContent, 120) || undefined,
          x: Math.round(rect.left), y: Math.round(rect.top),
          width: Math.round(rect.width), height: Math.round(rect.height),
        },
      };
    }

    if (name === 'waitForLoadState') {
      var want = action.state || 'load';
      var dl = Date.now() + (action.timeoutMs || DEFAULT_TIMEOUT_MS);
      while (Date.now() < dl) {
        var st = document.readyState;
        if (want === 'domcontentloaded' && (st === 'interactive' || st === 'complete')) return { value: null };
        if (want === 'load' && st === 'complete') return { value: null };
        await sleep(50);
      }
      throw new Error('waitForLoadState timeout: ' + want);
    }

    if (name === 'waitForURL') {
      var pattern = String(action.url || '');
      var dl2 = Date.now() + (action.timeoutMs || DEFAULT_TIMEOUT_MS);
      // 含通配符的 URL 模式逐字符转写为正则（* → .*，其余正则元字符转义）
      var toRegExp = function (glob) {
        var specials = '.+?^$()|[]{}\\\\';
        var out = '^';
        for (var ci = 0; ci < glob.length; ci++) {
          var ch = glob[ci];
          if (ch === '*') out += '.*';
          else if (specials.indexOf(ch) >= 0) out += '\\\\' + ch;
          else out += ch;
        }
        return new RegExp(out + '$');
      };
      var re = pattern.indexOf('*') >= 0 ? toRegExp(pattern) : null;
      while (Date.now() < dl2) {
        var href = window.location.href;
        if (re ? re.test(href) : href === pattern) return { value: null };
        await sleep(50);
      }
      throw new Error('waitForURL timeout: ' + pattern);
    }

    if (name === 'locator') {
      var els = await resolveLocator(action.selector, action.timeoutMs, { visible: true });
      var el = firstVisible(els);
      var op = action.operation;

      switch (op) {
        case 'count': return { value: els.length };
        case 'textContent': return { value: el.textContent };
        case 'innerText': return { value: el.innerText !== undefined ? el.innerText : el.textContent };
        case 'allTextContents': return { value: Array.prototype.map.call(els, function (e2) { return e2.textContent; }) };
        case 'getAttribute': return { value: el.getAttribute(action.attribute) };
        case 'isVisible': return { value: isVisible(el) };
        case 'isEnabled': return { value: el.disabled !== true };
        case 'evaluate': {
          var lfn = new Function('return (' + action.expression + ')')();
          return { value: await lfn(el, action.arg) };
        }
        case 'click': {
          var c = centerOf(el);
          return { input: 'click', x: c.x, y: c.y, clickCount: 1 };
        }
        case 'dblclick': {
          var dc = centerOf(el);
          return { input: 'click', x: dc.x, y: dc.y, clickCount: 2 };
        }
        case 'hover': {
          var hc = centerOf(el);
          return { input: 'hover', x: hc.x, y: hc.y };
        }
        case 'fill': {
          el.focus();
          // replace:false 为追加语义（SDK 的 locator.type 走 fill+replace:false）
          var text = action.replace === false
            ? String(el.value || '') + String(action.value == null ? '' : action.value)
            : String(action.value == null ? '' : action.value);
          setNativeValue(el, text);
          return { value: null };
        }
        case 'press': {
          el.focus();
          // SDK 的按键字段名为 value（locator.press 传 {value: 键名}）
          return { input: 'press', key: action.value || action.key, modifiers: action.modifiers };
        }
        case 'check': case 'uncheck': case 'setChecked': {
          var wantChecked = op === 'check' ? true : op === 'uncheck' ? false : !!action.checked;
          if (el.checked === wantChecked) return { value: null };
          var cc = centerOf(el);
          return { input: 'click', x: cc.x, y: cc.y, clickCount: 1 };
        }
        case 'selectOption': {
          el.focus();
          var wanted = (action.selections || []).map(function (s) { return String(s.value != null ? s.value : s.label != null ? s.label : s); });
          Array.prototype.forEach.call(el.options, function (opt) {
            opt.selected = wanted.indexOf(opt.value) >= 0 || wanted.indexOf(opt.textContent.trim()) >= 0;
          });
          el.dispatchEvent(new Event('change', { bubbles: true }));
          return { value: null };
        }
        case 'waitFor': {
          var state = action.state || 'visible';
          var ok = state === 'hidden' || state === 'detached' ? false : true;
          return { value: ok };
        }
        case 'scroll': {
          el.scrollIntoView({ block: action.block || 'center' });
          return { value: null };
        }
        case 'downloadMedia': {
          var dm = centerOf(el);
          return { input: 'click', x: dm.x, y: dm.y, clickCount: 1 };
        }
        default:
          throw new Error('unsupported locator operation: ' + op);
      }
    }

    throw new Error('unsupported playwright action: ' + name);
  }

  window.__inappBrowser = { dispatch: dispatch };
})();`;

module.exports = { PAGE_RUNTIME_SOURCE };
