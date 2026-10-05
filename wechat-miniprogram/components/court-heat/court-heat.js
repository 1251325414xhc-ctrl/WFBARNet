const canvasUtil = require('../../utils/canvas')

// 标准羽毛球场坐标系（与桌面端 user_report.py 的 _render_court_svg 完全一致）
const COURT_W = 610
const COURT_H = 1340

Component({
  properties: {
    points: { type: Array, value: [] },
    showGrid: { type: Boolean, value: true }
  },

  data: { failed: false },

  lifetimes: {
    attached() {
      this._ready = false
    },
    ready() {
      this._ready = true
      this.render()
    }
  },

  observers: {
    points: function () {
      if (this._ready) this.render()
    }
  },

  methods: {
    onCanvasMissing() {
      this.setData({ failed: true })
    },

    render() {
      const self = this
      canvasUtil.initCanvas(this, '#court', function (ctx, size) {
        const w = size.width
        const h = size.height
        const scale = Math.min(w / COURT_W, h / COURT_H)
        const offsetX = (w - COURT_W * scale) / 2
        const offsetY = (h - COURT_H * scale) / 2

        function X(v) {
          return offsetX + v * scale
        }
        function Y(v) {
          return offsetY + v * scale
        }

        // 场地底板（圆角矩形）
        const bx = X(18)
        const by = Y(18)
        const bw = 574 * scale
        const bh = 1304 * scale
        const br = Math.min(26 * scale, bw / 2, bh / 2)
        ctx.beginPath()
        ctx.moveTo(bx + br, by)
        ctx.lineTo(bx + bw - br, by)
        ctx.arcTo(bx + bw, by, bx + bw, by + br, br)
        ctx.lineTo(bx + bw, by + bh - br)
        ctx.arcTo(bx + bw, by + bh, bx + bw - br, by + bh, br)
        ctx.lineTo(bx + br, by + bh)
        ctx.arcTo(bx, by + bh, bx, by + bh - br, br)
        ctx.lineTo(bx, by + br)
        ctx.arcTo(bx, by, bx + br, by, br)
        ctx.closePath()
        ctx.fillStyle = '#0e704a'
        ctx.fill()

        // 外框
        ctx.strokeStyle = 'rgba(255,255,255,0.92)'
        ctx.lineWidth = Math.max(1.2, 4 * scale)
        ctx.strokeRect(X(44), Y(44), 522 * scale, 1252 * scale)

        // 内部线
        ctx.lineWidth = Math.max(1, 3 * scale)
        const hLines = [242, 470, 670, 870, 1098]
        hLines.forEach(function (y) {
          ctx.beginPath()
          ctx.moveTo(X(44), Y(y))
          ctx.lineTo(X(566), Y(y))
          ctx.stroke()
        })
        ctx.beginPath()
        ctx.moveTo(X(305), Y(44))
        ctx.lineTo(X(305), Y(470))
        ctx.moveTo(X(305), Y(870))
        ctx.lineTo(X(305), Y(1296))
        ctx.moveTo(X(92), Y(44))
        ctx.lineTo(X(92), Y(1296))
        ctx.moveTo(X(518), Y(44))
        ctx.lineTo(X(518), Y(1296))
        ctx.stroke()

        // 球网
        ctx.lineWidth = Math.max(1.4, 5 * scale)
        ctx.strokeStyle = 'rgba(255,255,255,0.98)'
        ctx.beginPath()
        ctx.moveTo(X(44), Y(670))
        ctx.lineTo(X(566), Y(670))
        ctx.stroke()

        // 热区
        const points = self.data.points || []
        points.forEach(function (item) {
          const cx = X(Number(item.x) || COURT_W / 2)
          const cy = Y(Number(item.y) || COURT_H / 2)
          const r = Math.max(10, (Number(item.radius) || 48) * scale)
          const alpha = Math.max(0.2, Math.min(0.95, Number(item.intensity) || 0.6))
          const grad = ctx.createRadialGradient(cx, cy, r * 0.1, cx, cy, r)
          grad.addColorStop(0, 'rgba(255,236,150,' + alpha + ')')
          grad.addColorStop(0.45, 'rgba(238,112,93,' + alpha * 0.85 + ')')
          grad.addColorStop(1, 'rgba(238,112,93,0)')
          ctx.beginPath()
          ctx.fillStyle = grad
          ctx.arc(cx, cy, r, 0, Math.PI * 2)
          ctx.fill()
          ctx.beginPath()
          ctx.strokeStyle = 'rgba(255,255,255,' + Math.min(0.9, alpha + 0.2) + ')'
          ctx.lineWidth = 1.2
          ctx.arc(cx, cy, r * 0.34, 0, Math.PI * 2)
          ctx.stroke()
        })
      })
    }
  }
})
