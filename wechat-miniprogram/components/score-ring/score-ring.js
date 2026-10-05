const canvasUtil = require('../../utils/canvas')

Component({
  properties: {
    score: { type: Number, value: 0 },
    max: { type: Number, value: 100 },
    tone: { type: String, value: 'light' }, // light | dark
    caption: { type: String, value: '' },
    sub: { type: String, value: '' }
  },

  data: {
    failed: false
  },

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
    'score, max, tone': function () {
      if (this._ready) this.render()
    }
  },

  methods: {
    onCanvasMissing() {
      this.setData({ failed: true })
    },

    render() {
      const self = this
      canvasUtil.initCanvas(this, '#ring', function (ctx, size) {
        const w = size.width
        const h = size.height
        const dark = self.data.tone === 'dark'
        const max = self.data.max || 100
        const value = Math.max(0, Math.min(max, Number(self.data.score) || 0))
        const ratio = value / max
        const cx = w / 2
        const cy = h / 2
        const thickness = Math.max(10, Math.min(w, h) * 0.115)
        const radius = Math.min(w, h) / 2 - thickness / 2 - 4
        const start = -Math.PI / 2
        const end = start + Math.PI * 2 * ratio

        ctx.lineCap = 'round'
        ctx.lineWidth = thickness

        // 轨道
        ctx.beginPath()
        ctx.strokeStyle = dark ? 'rgba(248,255,242,0.20)' : 'rgba(16,35,29,0.09)'
        ctx.arc(cx, cy, radius, 0, Math.PI * 2)
        ctx.stroke()

        // 进度
        if (ratio > 0) {
          const grad = ctx.createLinearGradient(0, 0, w, h)
          const main = canvasUtil.scoreColor(value)
          grad.addColorStop(0, dark ? '#b6e95e' : main)
          grad.addColorStop(1, main)
          ctx.beginPath()
          ctx.strokeStyle = grad
          ctx.arc(cx, cy, radius, start, end)
          ctx.stroke()
        }
      })
    }
  }
})
