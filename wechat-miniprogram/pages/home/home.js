const api = require('../../utils/api')
const fmt = require('../../utils/format')

const QUICK = [
  { key: 'records', label: '训练记录', desc: '全部回合复盘', tone: 'court', route: '/pages/records/records', tab: true },
  { key: 'analysis', label: '智能分析', desc: '上传视频出报告', tone: 'sky', route: '/pages/analysis/analysis', tab: false },
  { key: 'performance', label: '整体表现', desc: '趋势与能力对比', tone: 'gold', route: '/pages/performance/performance', tab: false },
  { key: 'booking', label: '场地预约', desc: '选场选时段', tone: 'coral', route: '/pages/booking/booking', tab: true }
]

function greeting() {
  const h = new Date().getHours()
  if (h < 6) return '夜深了，注意休息'
  if (h < 11) return '早上好，今天也要动起来'
  if (h < 14) return '中午好，来一场短回合吧'
  if (h < 18) return '下午好，适合练步法'
  return '晚上好，球馆见'
}

Page({
  data: {
    user: {},
    venue: {},
    greeting: greeting(),
    quick: QUICK,
    latest: null,
    deltaLabel: '',
    latestMeta: '',
    nextBooking: null,
    countdown: '',
    trend: [],
    scoreStats: null,
    tip: null,
    loading: true
  },

  onShow() {
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 0 })
    }
    this.load()
  },

  onPullDownRefresh() {
    this.load().then(function () {
      wx.stopPullDownRefresh()
    })
  },

  load() {
    const self = this
    return Promise.all([
      api.getUserProfile(),
      api.getVenue(),
      api.getTrainingRecords('all'),
      api.getPerformance('all'),
      api.getBookings('mine')
    ]).then(function (res) {
      const user = res[0]
      const venue = res[1]
      const records = res[2]
      const perf = res[3]
      const bookings = res[4]

      const latest = perf.latest
      const latestScore = latest ? latest.report.summary.overall_score : 0
      const prevScore = perf.previous ? perf.previous.report.summary.overall_score : latestScore
      const delta = latestScore - prevScore
      const doneCount = records.filter(function (r) { return r.status === 'done' }).length
      const pendingCount = records.length - doneCount

      // 下一次场地预约
      const todayKey = fmt.dateKey(new Date())
      const upcoming = bookings
        .filter(function (b) {
          return b.date >= todayKey && (b.status === 'paid' || b.status === 'pending')
        })
        .sort(function (a, b) {
          return a.date + fmt.pad2(Math.floor(a.startMinutes / 60)) <
            b.date + fmt.pad2(Math.floor(b.startMinutes / 60)) ? -1 : 1
        })[0]

      let nextBooking = null
      if (upcoming) {
        nextBooking = Object.assign({}, upcoming, {
          dateLabel: fmt.fmtDateCn(upcoming.date),
          weekLabel: fmt.weekLabel(upcoming.date),
          relative: fmt.relativeDay(upcoming.date),
          statusLabel:
            upcoming.status === 'paid' ? '已支付' : upcoming.status === 'pending' ? '待支付' : '已确认',
          amountLabel: fmt.fmtMoney(upcoming.amount)
        })
      }

      self.setData({
        user: Object.assign({}, user, { doneCount: doneCount, pendingCount: pendingCount }),
        venue: venue,
        latest: latest
          ? {
              id: latest.id,
              score: latestScore,
              levelLabel: latest.report.summary.level_label,
              levelNote: latest.report.summary.level_note,
              dateLabel: latest.dateLabel,
              sessionKind: latest.sessionKind,
              courtName: latest.courtName,
              coachName: latest.coachName,
              metrics: latest.report.summary.metrics,
              ballSpeed: latest.report.summary.ball_speed,
              hitCount: latest.report.players.user.hit_count
            }
          : null,
        deltaLabel: delta > 0 ? '较上次 +' + delta : delta < 0 ? '较上次 ' + delta : '较上次持平',
        latestMeta: latest ? latest.dateLabel + ' · ' + latest.courtName + ' · ' + latest.sessionKind : '',
        nextBooking: nextBooking,
        trend: perf.trend.slice(-6),
        scoreStats: perf.scoreStats,
        tip: latest ? latest.report.improvements[0] : null,
        loading: false
      })
    })
  },

  onQuickTap(e) {
    const route = e.currentTarget.dataset.route
    const tab = e.currentTarget.dataset.tab
    if (tab) wx.switchTab({ url: route })
    else wx.navigateTo({ url: route })
  },

  goReport() {
    if (!this.data.latest) {
      wx.showToast({ title: '还没有分析报告', icon: 'none' })
      return
    }
    wx.navigateTo({ url: '/pages/report/report?id=' + this.data.latest.id })
  },

  goPerformance() {
    wx.navigateTo({ url: '/pages/performance/performance' })
  },

  goOrders() {
    wx.navigateTo({ url: '/pages/orders/orders' })
  },

  goAnalysis() {
    wx.navigateTo({ url: '/pages/analysis/analysis' })
  },

  goBooking() {
    wx.switchTab({ url: '/pages/booking/booking' })
  }
})
