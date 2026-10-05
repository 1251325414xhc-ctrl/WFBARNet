const api = require('../../utils/api')
const fmt = require('../../utils/format')

const RANGES = [
  { key: '30', label: '近 30 天', days: 30 },
  { key: '90', label: '近 90 天', days: 90 },
  { key: 'all', label: '全部', days: 0 }
]

const METRICS = [
  { key: 'score', label: '综合评分', unit: '分', accent: '#0e704a' },
  { key: 'distance', label: '累计跑动', unit: 'm', accent: '#2f9fb0' },
  { key: 'passive', label: '被动击球占比', unit: '%', accent: '#d4633f', lowerIsBetter: true },
  { key: 'ballSpeed', label: '最高球速', unit: 'km/h', accent: '#d79b25' }
]

Page({
  data: {
    ranges: RANGES,
    activeRange: 'all',
    metrics: METRICS,
    activeMetric: 'score',
    perf: null,
    trend: [],
    radar: [],
    metricSeries: [],
    activeMetricMeta: METRICS[0],
    loading: true
  },

  onShow() {
    this.load()
  },

  onPullDownRefresh() {
    this.load().then(function () {
      wx.stopPullDownRefresh()
    })
  },

  load() {
    const self = this
    return api.getPerformance('all').then(function (perf) {
      self.applyRange(perf, self.data.activeRange)
    })
  },

  applyRange(perf, rangeKey) {
    const range = RANGES.filter(function (r) {
      return r.key === rangeKey
    })[0]
    let items = perf.items
    if (range && range.days > 0) {
      const limit = Date.now() - range.days * 86400000
      items = items.filter(function (item) {
        return new Date(item.createdAt).getTime() >= limit
      })
    }
    if (!items.length) items = perf.items.slice(0, 1)

    const trend = items
      .slice()
      .reverse()
      .map(function (item) {
        return { label: item.dayLabel, value: item.report.summary.overall_score }
      })

    const latest = items[0]
    const earliest = items[items.length - 1]
    const radar = latest
      ? latest.report.dimensions.map(function (d) {
          let sum = 0
          let count = 0
          items.forEach(function (item) {
            const found = item.report.dimensions.filter(function (x) {
              return x.name === d.name
            })[0]
            if (found) {
              sum += found.score
              count += 1
            }
          })
          const avg = count ? Math.round(sum / count) : 0
          return {
            name: d.name,
            score: d.score,
            benchmark: d.benchmark,
            average: avg,
            delta: d.score - avg,
            deltaText: d.score - avg > 0 ? '+' + (d.score - avg) : String(d.score - avg)
          }
        })
      : []

    const metricTrends = {}
    perf.metricTrends.forEach(function (mt) {
      const series = items
        .slice()
        .reverse()
        .map(function (item) {
          const point = mt.series.filter(function (p) {
            return p.label === item.dayLabel
          })[0]
          return {
            label: item.dayLabel,
            value: point ? point.value : 0
          }
        })
      metricTrends[mt.key] = series
    })

    const scores = trend.map(function (p) {
      return p.value
    })
    const best = scores.length ? Math.max.apply(null, scores) : 0
    const avgScore = scores.length
      ? Math.round(
          scores.reduce(function (a, b) {
            return a + b
          }, 0) / scores.length
        )
      : 0

    let totalDistance = 0
    let totalHits = 0
    let totalDuration = 0
    items.forEach(function (item) {
      totalDistance += item.report.players.user.distance_m
      totalHits += item.report.players.user.hit_count
      totalDuration += item.report.summary.rally_duration_s
    })

    this.setData({
      activeRange: rangeKey,
      perf: {
        count: items.length,
        firstDate: earliest ? earliest.dateLabel : '',
        lastDate: latest ? latest.dateLabel : '',
        latest: latest
          ? {
              id: latest.id,
              dateLabel: latest.dateLabel,
              score: latest.report.summary.overall_score,
              levelLabel: latest.report.summary.level_label,
              sessionKind: latest.sessionKind
            }
          : null,
        scoreStats: {
          best: best,
          average: avgScore,
          delta: best - scores[0],
          recentDelta: scores[scores.length - 1] - scores[scores.length - 2] || 0
        },
        totals: {
          sessions: items.length,
          distanceM: Math.round(totalDistance),
          hits: totalHits,
          durationText: fmt.fmtSeconds(totalDuration)
        },
        percentile: perf.percentile,
        milestones: perf.milestones
      },
      trend: trend,
      radar: radar,
      metricSeries: metricTrends[this.data.activeMetric] || []
    })
  },

  onRangeTap(e) {
    this.setData({ activeRange: e.currentTarget.dataset.key })
    const self = this
    api.getPerformance('all').then(function (perf) {
      self.applyRange(perf, e.currentTarget.dataset.key)
    })
  },

  onMetricTap(e) {
    const key = e.currentTarget.dataset.key
    const meta = METRICS.filter(function (m) {
      return m.key === key
    })[0]
    this.setData({ activeMetric: key, activeMetricMeta: meta })
    const self = this
    api.getPerformance('all').then(function (perf) {
      self.applyRange(perf, self.data.activeRange)
    })
  },

  goReport(e) {
    wx.navigateTo({ url: '/pages/report/report?id=' + e.currentTarget.dataset.id })
  }
})
