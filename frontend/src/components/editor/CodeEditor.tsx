import Editor from '@monaco-editor/react'

interface CodeEditorProps {
  content: string
  language: string
  readOnly: boolean
  onChange?: (value: string) => void
}

// Monaco 编辑器封装：深色主题、自适应容器大小。
// 用 defaultValue 初始化 + onChange 上报，避免受控回灌打断 Monaco 自身 undo 栈；
// 内容需要整体重置（如重新加载、切换标签）时，由上层通过 key 变化触发重挂载。
function CodeEditor({ content, language, readOnly, onChange }: CodeEditorProps) {
  return (
    <Editor
      height="100%"
      theme="vs-dark"
      language={language}
      defaultValue={content}
      onChange={(value) => onChange?.(value ?? '')}
      options={{
        readOnly,
        minimap: { enabled: false },
        fontSize: 13,
        scrollBeyondLastLine: false,
        automaticLayout: true,
      }}
    />
  )
}

export default CodeEditor
