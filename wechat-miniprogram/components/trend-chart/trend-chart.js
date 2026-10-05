const canvasUtil = require('../../utils/canvas')

Component({
  properties: {
    series: { type: Array, value: [] },
    unit: { type: String, value: '' },
    accent: { type: String, value: '#0e704a' },
    lowerIsBetter: { type: Boolean, value: false }
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
    'series, accent, unit': function () {
      if (this._ready) this.render()
    }
  },

  methods: {
    onCanvasMissing() {
      this.setData({ failed: true })
    },

    render() {
      const self = this
      canvasUtil.initCanvas(this, '#trend', function (ctx, size) {
        const items = (self.data.series || []).filter(function (item) {
          return typeof item.value === 'number'
        })
        if (items.length < 2) return
        const w = size.width
        const h = size.height
        const padLeft = 34
        const padRight = 14
        const padTop = 22
        const padBottom = 26
        const plotW = w - padLeft - padRight
        const plotH = h - padTop - padBottom

        const values = items.map(function (item) {
          return Number(item.value)
        })
        let min = Math.min.apply(null, values)
        let max = Math.max.apply(null, values)
        if (max === min) {
          max = min + 1
        }
        const pad = (max - min) * 0.28
        min -= pad
        max += pad

        function px(i) {
          return padLeft + (plotW * i) / (items.length - 1)
        }
        function py(v) {
          return padTop + plotH - ((v - min) / (max - min)) * plotH
        }

        // 网格 + 刻度
        ctx.font = '10px sans-serif'
        ctx.textAlign = 'right'
        ctx.textBaseline = 'middle'
        for (let g = 0; g <= 3; g += 1) {
          const y = padTop + (plotH * g) / 3
          ctx.beginPath()
          ctx.strokeStyle = 'rgba(16,35,29,0.08)'
          ctx.lineWidth = 1
          ctx.moveTo(padLeft, y)
          ctx.lineTo(w - padRight, y)
          ctx.stroke()
          const value = max - ((max - min) * g) / 3
          ctx.fillStyle = 'rgba(95,112,104,0.85)'
          ctx.fillText(String(Math.round(value)), padLeft - 6, y)
        }

        // 面积
        const grad = ctx.createLinearGradient(0, padTop, 0, padTop + plotH)
        grad.addColorStop(0, 'rgba(14,112,74,0.26)')
        grad.addColorStop(1, 'rgba(14,112,74,0.02)')
        ctx.beginPath()
        ctx.moveTo(px(0), py(values[0]))
        values.forEach(function (v, i) {
          if (i > 0) ctx.lineTo(px(i), py(v))
        })
        ctx.lineTo(px(values.length - 1), padTop + plotH)
        ctx.lineTo(px(0), padTop + plotH)
        ctx.closePath()
        ctx.fillStyle = grad
        ctx.fill()

        // 折线
        ctx.beginPath()
        values.forEach(function (v, i) {
          if (i === 0) ctx.moveTo(px(i), py(v))
          else ctx.lineTo(px(i), py(v))
        })
        ctx.strokeStyle = self.data.accent || '#0e704a'
        ctx.lineWidth = 2.4
        ctx.lineJoin = 'round'
        ctx.stroke()

        // 数据点
        values.forEach(function (v, i) {
          ctx.beginPath()
          ctx.fillStyle = '#ffffff'
          ctx.arc(px(i), py(v), 3.4, 0, Math.PI * 2)
          ctx.fill()
          ctx.lineWidth = 2
          ctx.strokeStyle = self.data.accent || '#0e704a'
          ctx.stroke()
        })

        // 末端数值
        const lastIndex = values.length - 1
        ctx.font = 'bold 11px sans-serif'
        ctx.textAlign = 'right'
        ctx.fillStyle = '#10231d'
        ctx.fillText(Math.round(values[lastIndex]) + (self.data.unit || ''), px(lastIndex), py(values[lastIndex]) - 12)

        // 横轴标签：首 / 中 / 末
        ctx.font = '10px sans-serif'
        ctx.textBaseline = 'top'
        ctx.fillStyle = 'rgba(95,112,104,0.9)'
        const marks = [0, Math.floor(lastIndex / 2), lastIndex]
        marks.forEach(function (i, k) {
          let align = 'center'
          if (k === 0) align = 'left'
          if (k === 2) align = 'right'
          ctx.textAlign = align
          ctx.fillText(String(items[i].label || ''), px(i), padTop + plotH + 8)
        })
      })
    }
  }
})
