import { useRef, useEffect, useState, useCallback } from 'react'

interface ResizerProps {
  // 拖拽时回调，传入鼠标相对位移 deltaPx，由父组件决定如何更新宽度
  onResize: (deltaPx: number) => void
  // 拖拽方向
  direction: 'horizontal' | 'vertical'
  // 拖拽时是否反方向（比如向左拖让侧边栏变宽）
  invert?: boolean
}

// 可拖拽的分隔条：鼠标按下后监听全局移动，松开时移除监听
// 鼠标悬停时显示中性灰细线，拖拽时显示更明显的指示
function Resizer({ onResize, direction, invert = false }: ResizerProps) {
  const [isDragging, setIsDragging] = useState(false)
  const [isHovered, setIsHovered] = useState(false)
  // 记录上一次鼠标位置，算增量
  const lastPosRef = useRef(0)

  const isHorizontal = direction === 'horizontal'

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setIsDragging(true)
    lastPosRef.current = isHorizontal ? e.clientX : e.clientY
  }, [isHorizontal])

  useEffect(() => {
    if (!isDragging) return

    const handleMouseMove = (e: MouseEvent) => {
      const currentPos = isHorizontal ? e.clientX : e.clientY
      let delta = currentPos - lastPosRef.current
      // 反方向时取反（比如侧边栏在左边，向左拖应该让它变宽）
      if (invert) delta = -delta
      onResize(delta)
      lastPosRef.current = currentPos
    }

    const handleMouseUp = () => {
      setIsDragging(false)
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    // 拖拽时禁用文本选中
    document.body.style.cursor = isHorizontal ? 'col-resize' : 'row-resize'
    document.body.style.userSelect = 'none'

    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [isDragging, onResize, invert, isHorizontal])

  return (
    <div
      onMouseDown={handleMouseDown}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        // 横向分隔条占 4px 宽，纵向占 4px 高
        width: isHorizontal ? '4px' : '100%',
        height: isHorizontal ? '100%' : '4px',
        cursor: isHorizontal ? 'col-resize' : 'row-resize',
        position: 'relative',
        flexShrink: 0,
        backgroundColor: isDragging
          ? 'var(--border-strong)'
          : isHovered
            ? 'var(--border-strong)'
            : 'transparent',
        transition: isDragging ? 'none' : 'background var(--transition-fast)',
        zIndex: 10,
      }}
    >
      {/* 中间的拖拽指示线，悬停或拖拽时显示 */}
      {(isHovered || isDragging) && (
        <div
          style={{
            position: 'absolute',
            // 横向分隔条画一条竖线，纵向画一条横线
            top: isHorizontal ? 0 : '50%',
            left: isHorizontal ? '50%' : 0,
            width: isHorizontal ? '1px' : '100%',
            height: isHorizontal ? '100%' : '1px',
            backgroundColor: isDragging ? 'var(--text-secondary)' : 'var(--border-strong)',
            transform: isHorizontal
              ? 'translateX(-50%)'
              : 'translateY(-50%)',
            opacity: isDragging ? 1 : 0.6,
            boxShadow: isDragging ? '0 0 8px var(--focus-ring)' : 'none',
          }}
        />
      )}
    </div>
  )
}

export default Resizer
