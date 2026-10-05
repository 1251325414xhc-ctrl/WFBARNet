const canvasUtil = require('../../utils/canvas')

Component({
  properties: {
    dimensions: { type: Array, value: [] },
    showBenchmark: { type: Boolean, value: true },
    tone: { type: String, value: 'light' }
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
    'dimensions, showBenchmark, tone': function () {
      if (this._ready) this.render()
    }
  },

  methods: {
    onCanvasMissing() {
      this.setData({ failed: true })
    },

    render() {
      const self = this
      canvasUtil.initCanvas(this, '#radar', function (ctx, size) {
        const items = self.data.dimensions || []
        if (!items.length) return
        const dark = self.data.tone === 'dark'
        const w = size.width
        const h = size.height
        const cx = w / 2
        const cy = h / 2 + 4
        const count = items.length
        const radius = Math.min(w, h) / 2 - 44
        const labelRadius = radius + 22

        function point(index, r) {
          const angle = -Math.PI / 2 + (Math.PI * 2 * index) / count
          return [cx + Math.cos(angle) * r, cy + Math.sin(angle) * r]
        }

        function polygon(values) {
          ctx.beginPath()
          for (let i = 0; i < count; i += 1) {
            const p = point(i, values[i])
            if (i === 0) ctx.moveTo(p[0], p[1])
            else ctx.lineTo(p[0], p[1])
          }
          ctx.closePath()
        }

        // 网格
        ctx.lineWidth = 1
        ctx.strokeStyle = dark ? 'rgba(248,255,242,0.16)' : 'rgba(16,35,29,0.10)'
        const levels = [0.2, 0.4, 0.6, 0.8, 1]
        levels.forEach(function (level) {
          const values = items.map(function () {
            return radius * level
          })
          polygon(values)
          ctx.stroke()
        })

        // 轴线
        for (let i = 0; i < count; i += 1) {
          const p = point(i, radius)
          ctx.beginPath()
          ctx.strokeStyle = dark ? 'rgba(248,255,242,0.22)' : 'rgba(16,35,29,0.14)'
          ctx.moveTo(cx, cy)
          ctx.lineTo(p[0], p[1])
          ctx.stroke()
        }

        // 基准多边形（虚线金色）
        if (self.data.showBenchmark) {
          const benchValues = items.map(function (item) {
            const bench = Number(item.benchmark) || 80
            return radius * Math.max(0, Math.min(1, bench / 100))
          })
          if (ctx.setLineDash) ctx.setLineDash([5, 5])
          polygon(benchValues)
          ctx.strokeStyle = 'rgba(240,190,73,0.95)'
          ctx.lineWidth = 1.5
          ctx.stroke()
          ctx.fillStyle = 'rgba(240,190,73,0.10)'
          ctx.fill()
          if (ctx.setLineDash) ctx.setLineDash([])
        }

        // 当前值多边形
        const valueValues = items.map(function (item) {
          const score = Number(item.score) || 0
          return radius * Math.max(0, Math.min(1, score / 100))
        })
        polygon(valueValues)
        const grad = ctx.createRadialGradient(cx, cy, radius * 0.1, cx, cy, radius)
        grad.addColorStop(0, dark ? 'rgba(182,233,94,0.55)' : 'rgba(14,112,74,0.42)')
        grad.addColorStop(1, dark ? 'rgba(107,197,210,0.30)' : 'rgba(107,197,210,0.28)')
        ctx.fillStyle = grad
        ctx.fill()
        ctx.lineWidth = 2.5
        ctx.strokeStyle = dark ? '#b6e95e' : '#0e704a'
        ctx.stroke()

        // 数据点
        for (let i = 0; i < count; i += 1) {
          const p = point(i, valueValues[i])
          ctx.beginPath()
          ctx.fillStyle = dark ? '#b6e95e' : '#0e704a'
          ctx.arc(p[0], p[1], 3.2, 0, Math.PI * 2)
          ctx.fill()
        }

        // 标签
        ctx.font = 'bold 11px sans-serif'
        ctx.textBaseline = 'middle'
        for (let i = 0; i < count; i += 1) {
          const p = point(i, labelRadius)
          const name = String(items[i].name || '')
          const score = Math.round(Number(items[i].score) || 0)
          let align = 'center'
          if (p[0] < cx - 10) align = 'right'
          else if (p[0] > cx + 10) align = 'left'
          ctx.textAlign = align
          ctx.fillStyle = dark ? 'rgba(248,255,242,0.92)' : '#10231d'
          ctx.fillText(name, p[0], p[1] - 6)
          ctx.font = 'bold 11px sans-serif'
          ctx.fillStyle = dark ? 'rgba(182,233,94,0.95)' : '#0e704a'
          ctx.fillText(String(score), p[0], p[1] + 8)
          ctx.font = 'bold 11px sans-serif'
        }
      })
    }
  }
})
