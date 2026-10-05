const api = require('../../utils/api')
const fmt = require('../../utils/format')

Page({
  data: {
    user: {},
    venue: {},
    stats: null,
    settings: {
      remind: true,
      pushReport: true,
      metric: true
    },
    version: '1.0.0',
    pipeline: 'TrackNetV3 + YOLO Pose + CourtKeyNet 单应性 + BST 击球识别'
  },

  onShow() {
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 3 })
    }
    this.load()
  },

  load() {
    const self = this
    return Promise.all([api.getUserProfile(), api.getVenue(), api.getPerformance('all')]).then(function (res) {
      const perf = res[2]
      let totalDuration = 0
      let totalDistance = 0
      perf.items.forEach(function (item) {
        totalDuration += item.report.summary.rally_duration_s
        totalDistance += item.report.players.user.distance_m
      })
      self.setData({
        user: res[0],
        venue: res[1],
        stats: {
          sessions: perf.count,
          durationText: Math.round(totalDuration / 60) + ' 分钟',
          distanceText: Math.round(totalDistance) + ' m',
          averageScore: perf.scoreStats.average,
          bestScore: perf.scoreStats.best,
          level: perf.latest ? perf.latest.report.summary.level_label : '--',
          percentile: perf.percentile
        }
      })
    })
  },

  onSettingChange(e) {
    const key = e.currentTarget.dataset.key
    const settings = Object.assign({}, this.data.settings)
    settings[key] = e.detail.value
    this.setData({ settings: settings })
  },

  goOrders() {
    wx.navigateTo({ url: '/pages/orders/orders' })
  },

  goRecords() {
    wx.switchTab({ url: '/pages/records/records' })
  },

  goPerformance() {
    wx.navigateTo({ url: '/pages/performance/performance' })
  },

  goAnalysis() {
    wx.navigateTo({ url: '/pages/analysis/analysis' })
  },

  goMerchant() {
    wx.navigateTo({ url: '/pages/merchant/workbench/workbench' })
  },

  goReport() {
    wx.navigateTo({ url: '/pages/report/report?id=rec-08' })
  },

  copyVenue() {
    wx.setClipboardData({
      data: this.data.venue.address,
      success: function () {
        wx.showToast({ title: '地址已复制', icon: 'none' })
      }
    })
  },

  showAbout() {
    wx.showModal({
      title: '关于羽迹智能',
      content:
        'WFBARNet 羽毛球智能分析小程序 v' + this.data.version +
        '\n分析管线：' + this.data.pipeline +
        '\n报告为自动估计结果，适合训练复盘，不作为比赛裁判或等级认证依据。',
      showCancel: false,
      confirmText: '知道了'
    })
  },

  switchRole() {
    const self = this
    wx.showModal({
      title: '切换到商家端',
      content: '商家端包含场地管理、预约管理与客户分析任务。是否切换？',
      confirmText: '切换',
      success(res) {
        if (!res.confirm) return
        getApp().setRole('merchant')
        wx.navigateTo({ url: '/pages/merchant/workbench/workbench' })
      }
    })
  }
})
