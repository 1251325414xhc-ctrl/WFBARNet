// utils/canvas.js —— 小程序 canvas 2d 初始化（DPR 适配 + 尺寸重试 + 失败兜底）

function getPixelRatio() {
  try {
    if (wx.getWindowInfo) {
      return wx.getWindowInfo().pixelRatio || 2
    }
    return wx.getSystemInfoSync().pixelRatio || 2
  } catch (err) {
    return 2
  }
}

/**
 * 在自定义组件内初始化 canvas 2d 上下文。
 * @param {Object} component 组件实例（this）
 * @param {String} selector 例如 '#ring'
 * @param {Function} draw    draw(ctx, size)，size = { width, height, dpr } 均为 CSS 像素
 */
function initCanvas(component, selector, draw, attempt) {
  const tried = attempt || 0
  component
    .createSelectorQuery()
    .select(selector)
    .fields({ node: true, size: true })
    .exec(function (res) {
      const info = res && res[0]
      if (!info || !info.node) {
        if (typeof component.onCanvasMissing === 'function') component.onCanvasMissing()
        return
      }
      const width = Math.round(info.width)
      const height = Math.round(info.height)
      // 组件刚挂载时可能还没完成布局，宽高为 0 —— 重试几次
      if ((width < 2 || height < 2) && tried < 4) {
        setTimeout(function () {
          initCanvas(component, selector, draw, tried + 1)
        }, 90)
        return
      }
      const canvas = info.node
      const ctx = canvas.getContext('2d')
      const dpr = getPixelRatio()
      canvas.width = Math.max(1, Math.round(width * dpr))
      canvas.height = Math.max(1, Math.round(height * dpr))
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, width, height)
      try {
        draw(ctx, { width: width, height: height, dpr: dpr })
      } catch (err) {
        console.warn('[canvas] draw failed', err)
      }
    })
}

/** 数值 → 颜色（低分珊瑚红，高分球场绿） */
function scoreColor(score) {
  const s = Math.max(0, Math.min(100, Number(score) || 0))
  if (s >= 85) return '#0e704a'
  if (s >= 75) return '#3d8f52'
  if (s >= 65) return '#c9a02f'
  if (s >= 55) return '#e08a3c'
  return '#ee705d'
}

/** 文本省略：canvas 里没有 ellipsis，手动截断 */
function ellipsis(text, max) {
  const str = String(text == null ? '' : text)
  return str.length > max ? str.slice(0, max - 1) + '…' : str
}

module.exports = {
  initCanvas: initCanvas,
  getPixelRatio: getPixelRatio,
  scoreColor: scoreColor,
  ellipsis: ellipsis
}
