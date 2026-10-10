import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import ErrorBoundary from './components/ErrorBoundary'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {/* 根级最后防线：任何未被局部边界接住的渲染异常都落在这里，
        给出可读兜底与重载入口，而不是整窗黑屏 */}
    <ErrorBoundary reloadOnRetry>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
)
