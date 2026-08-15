// 设置面板 — 全屏 Modal/抽屉
// 左侧分区导航（插件/记忆/LLM 模型/子智能体/多智能体）+ 右侧详情区
// 从右侧滑出，支持 Esc/点遮罩关闭

import { useEffect, useState } from 'react'
import LLMSettingsSection from './LLMSettingsSection'
import PluginSettingsSection from './PluginSettingsSection'
import MemorySettingsSection from './MemorySettingsSection'
import SubagentSettingsSection from './SubagentSettingsSection'
import MultiagentPlaceholder from './MultiagentPlaceholder'
import SkillsSettingsSection from './SkillsSettingsSection'

// 七个分区
type SettingsSection = 'llm' | 'plugins' | 'memory' | 'subagents' | 'multiagent' | 'skills' | 'layout'

const sections: { id: SettingsSection; label: string; desc: string }[] = [
  { id: 'llm', label: 'LLM 模型', desc: '配置模型供应商与参数' },
  { id: 'plugins', label: '插件管理', desc: '启用/禁用已安装的插件' },
  { id: 'skills', label: '技能', desc: '管理技能：搜索、新建、导入、删除' },
  { id: 'memory', label: '记忆', desc: '管理记忆后端' },
  { id: 'subagents', label: '子智能体', desc: '查看内置子智能体' },
  { id: 'multiagent', label: '多智能体', desc: '多智能体编排（规划中）' },
  { id: 'layout', label: '界面', desc: '布局宽度与面板开关' },
]

// 界面分区：恢复默认布局
function LayoutSection({ onResetLayout }: { onResetLayout: () => void }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
      <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontFamily: 'var(--font-ui)' }}>
        把对话区/编辑区/文件树的宽度与面板开关重置为默认布局（用于恢复被拖乱或误调的界面）。
      </div>
      <button
        onClick={onResetLayout}
        style={{
          alignSelf: 'flex-start',
          padding: '8px 16px',
          cursor: 'pointer',
          background: 'var(--accent)',
          color: '#fff',
          border: 'none',
          borderRadius: 'var(--radius-sm)',
          fontSize: '12px',
          fontFamily: 'var(--font-ui)',
        }}
      >
        恢复默认布局
      </button>
    </div>
  )
}

interface SettingsModalProps {
  open: boolean
  onClose: () => void
  onResetLayout: () => void
}

function SettingsModal({ open, onClose, onResetLayout }: SettingsModalProps) {
  const [activeSection, setActiveSection] = useState<SettingsSection>('llm')

  // Esc 关闭
  useEffect(() => {
    if (!open) return
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [open, onClose])

  // 打开时禁止 body 滚动
  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden'
      return () => {
        document.body.style.overflow = ''
      }
    }
  }, [open])

  if (!open) return null

  // 渲染右侧详情
  const renderSection = () => {
    switch (activeSection) {
      case 'llm':
        return <LLMSettingsSection />
      case 'plugins':
        return <PluginSettingsSection />
      case 'memory':
        return <MemorySettingsSection />
      case 'subagents':
        return <SubagentSettingsSection />
      case 'multiagent':
        return <MultiagentPlaceholder />
      case 'skills':
        return <SkillsSettingsSection />
      case 'layout':
        return <LayoutSection onResetLayout={onResetLayout} />
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        display: 'flex',
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
        animation: 'fade-in-up 200ms ease',
      }}
      onClick={onClose}
    >
      {/* 从右滑出的面板 */}
      <div
        style={{
          marginLeft: 'auto',
          width: '760px',
          maxWidth: '90vw',
          height: '100%',
          backgroundColor: 'var(--bg-secondary)',
          borderLeft: '1px solid var(--border)',
          display: 'flex',
          boxShadow: 'var(--shadow-lg)',
          animation: 'fade-in-up 200ms ease',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 左侧分区导航 */}
        <div
          style={{
            width: '200px',
            flexShrink: 0,
            backgroundColor: 'var(--bg-base)',
            borderRight: '1px solid var(--border)',
            padding: '16px 8px',
            display: 'flex',
            flexDirection: 'column',
            gap: '2px',
          }}
        >
          <div
            style={{
              fontSize: '11px',
              textTransform: 'uppercase',
              color: 'var(--text-secondary)',
              fontWeight: 600,
              letterSpacing: '1.2px',
              padding: '0 8px 12px',
            }}
          >
            设置
          </div>
          {sections.map((sec) => {
            const isActive = activeSection === sec.id
            return (
              <button
                key={sec.id}
                onClick={() => setActiveSection(sec.id)}
                style={{
                  border: 'none',
                  background: isActive ? 'var(--accent-soft)' : 'transparent',
                  color: isActive ? 'var(--accent)' : 'var(--text-secondary)',
                  textAlign: 'left',
                  padding: '8px 10px',
                  borderRadius: 'var(--radius-sm)',
                  cursor: 'pointer',
                  fontSize: '13px',
                  fontFamily: 'var(--font-ui)',
                  transition: 'all var(--transition-fast)',
                }}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = 'var(--bg-tertiary)'
                    e.currentTarget.style.color = 'var(--text-primary)'
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = 'transparent'
                    e.currentTarget.style.color = 'var(--text-secondary)'
                  }
                }}
              >
                {sec.label}
              </button>
            )
          })}
        </div>

        {/* 右侧详情区 */}
        <div
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          {/* 顶部标题栏 + 关闭按钮 */}
          <div
            style={{
              height: '52px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '0 20px',
              borderBottom: '1px solid var(--border)',
              flexShrink: 0,
            }}
          >
            <div>
              <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
                {sections.find((s) => s.id === activeSection)?.label}
              </div>
              <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                {sections.find((s) => s.id === activeSection)?.desc}
              </div>
            </div>
            <button
              onClick={onClose}
              title="关闭"
              style={{
                border: 'none',
                background: 'transparent',
                color: 'var(--text-tertiary)',
                cursor: 'pointer',
                fontSize: '18px',
                padding: '4px 8px',
                borderRadius: 'var(--radius-sm)',
              }}
            >
              ✕
            </button>
          </div>

          {/* 内容滚动区 */}
          <div style={{ flex: 1, overflow: 'auto', padding: '20px' }}>
            {renderSection()}
          </div>
        </div>
      </div>
    </div>
  )
}

export default SettingsModal
