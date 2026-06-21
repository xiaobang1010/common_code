import Editor from '@monaco-editor/react'

interface CodeEditorProps {
  path: string
  content: string
  language: string
}

// Monaco 编辑器封装：只读、深色主题、自适应容器大小
function CodeEditor({ path, content, language }: CodeEditorProps) {
  return (
    <Editor
      height="100%"
      theme="vs-dark"
      language={language}
      value={content}
      path={path}
      options={{
        readOnly: true,
        minimap: { enabled: false },
        fontSize: 13,
        scrollBeyondLastLine: false,
        automaticLayout: true,
      }}
    />
  )
}

export default CodeEditor
