const api = require('../../utils/api')
const fmt = require('../../utils/format')

const TABS = [
  { key: 'overview', label: '概览' },
  { key: 'data', label: '数据' },
  { key: 'tips', label: '建议' }
]

// 与桌面端 _render_player_comparison 的指标与量程保持一致
const COMPARISON_METRICS = [
  { label: '累计跑动', key: 'distance_m', unit: 'm', max: 130 },
  { label: '平均速度', key: 'avg_speed_mps', unit: 'm/s', max: 2.2 },
  { label: '最大速度', key: 'max_speed_mps', unit: 'm/s', max: 5.5 },
  { label: '启动次数', key: 'start_count', unit: '次', max: 42 },
  { label: '急停次数', key: 'stop_count', unit: '次', max: 42 },
  { label: '被动击球', key: 'passive_hit_count', unit: '次', max: 20 }
]

Page({
  data: {
    id: '',
    tabs: TABS,
    active: 'overview',
    report: null,
    comparison: [],
    metrics: [],
    strokes: [],
    zones: [],
    heat: [],
    reliability: [],
    improvements: [],
    plan: [],
    events: [],
    loading: true
  },

  onLoad(options) {
    const id = (options && options.id) || ''
    this.setData({ id: id })
    this.load(id)
  },

  onPullDownRefresh() {
    this.load(this.data.id).then(function () {
      wx.stopPullDownRefresh()
    })
  },

  load(id) {
    const self = this
    return api
      .getTrainingReport(id)
      .then(function (report) {
        const user = report.players.user
        const opponent = report.players.opponent

        const comparison = COMPARISON_METRICS.map(function (item) {
          const uv = Number(user[item.key]) || 0
          const ov = Number(opponent[item.key]) || 0
          return {
            label: item.label,
            unit: item.unit,
            userText: uv.toFixed(1) + item.unit,
            oppText: ov.toFixed(1) + item.unit,
            userPercent: Math.max(2, Math.min(100, Math.round((uv / item.max) * 100))),
            oppPercent: Math.max(2, Math.min(100, Math.round((ov / item.max) * 100)))
          }
        })

        const maxStroke = report.stroke_distribution.reduce(function (a, b) {
          return Math.max(a, Number(b.value) || 0)
        }, 1)
        const strokes = report.stroke_distribution.map(function (item) {
          return {
            name: item.name,
            value: item.value,
            percent: Math.max(3, Math.round((item.value / maxStroke) * 100))
          }
        })

        const zoneTotal = report.zone_distribution.reduce(function (a, b) {
          return a + Number(b.value || 0)
        }, 0) || 1
        const zones = report.zone_distribution.map(function (item) {
          return {
            label: item.label,
            value: item.value,
            percent: Math.round((item.value / zoneTotal) * 100),
            share: ((item.value / zoneTotal) * 100).toFixed(1)
          }
        })

        const reliability = report.reliability.map(function (item) {
          return Object.assign({}, item, {
            percent: Math.round(item.value * 100),
            valueText: (item.value * 100).toFixed(1) + '%'
          })
        })

        const events = report.events.map(function (item, index) {
          return Object.assign({}, item, {
            key: 'ev-' + index,
            confidenceText: (item.confidence * 100).toFixed(1) + '%',
            stateText: item.passive ? '被动' : '主动',
            stateClass: item.passive ? 'danger' : 'ok'
          })
        })

        const riskLevel = reliability.reduce(function (a, b) {
          return Math.min(a, b.value)
        }, 1)

        self.setData({
          report: Object.assign({}, report, {
            meta: Object.assign({}, report.meta, {
              riskLevelText: (riskLevel * 100).toFixed(1) + '%',
              riskLevelHint:
                riskLevel >= 0.8
                  ? '数据可信度高，指标可直接横向比较'
                  : '存在偏低项，涉及落点与跑动的结论请保守解读'
            }),
            summary: Object.assign({}, report.summary, {
              takeaways: report.summary.takeaways,
              ballSpeedText: report.summary.ball_speed.max_kmh + ' km/h',
              avgSpeedText: report.summary.ball_speed.avg_kmh + ' km/h'
            })
          }),
          comparison: comparison,
          metrics: report.summary.metrics,
          strokes: strokes,
          zones: zones,
          heat: report.court_heat,
          reliability: reliability,
          improvements: report.improvements,
          plan: report.training_plan.map(function (item, index) {
            return Object.assign({}, item, { index: fmt.pad2(index + 1) })
          }),
          events: events,
          loading: false
        })
        wx.setNavigationBarTitle({ title: report.summary.level_label + ' · 分析报告' })
      })
      .catch(function () {
        self.setData({ loading: false, report: null })
        wx.showToast({ title: '报告加载失败', icon: 'none' })
      })
  },

  onTabTap(e) {
    this.setData({ active: e.currentTarget.dataset.key })
  },

  copySummary() {
    const report = this.data.report
    if (!report) return
    const lines = []
    lines.push('【羽毛球水平分析报告】' + report.meta.generated_at)
    lines.push('综合评分 ' + report.summary.overall_score + '/100 · ' + report.summary.level_label)
    lines.push('优势：' + report.summary.takeaways[1])
    lines.push('短板：' + report.summary.takeaways[2])
    lines.push('建议：')
    report.improvements.forEach(function (item, index) {
      lines.push('  ' + (index + 1) + '. ' + item.title + ' —— ' + item.suggestion)
    })
    wx.setClipboardData({
      data: lines.join('\n'),
      success: function () {
        wx.showToast({ title: '摘要已复制', icon: 'none' })
      }
    })
  },

  onShareAppMessage() {
    const report = this.data.report
    return {
      title: report
        ? '我的羽毛球分析报告：' + report.summary.overall_score + ' 分（' + report.summary.level_label + '）'
        : '羽毛球场智能分析报告',
      path: '/pages/report/report?id=' + this.data.id
    }
  },

  goAnalysis() {
    wx.navigateTo({ url: '/pages/analysis/analysis' })
  }
})
