const api = require('../../../utils/api')
const fmt = require('../../../utils/format')

Page({
  data: {
    venue: {},
    overview: null,
    revenue: [],
    tasks: [],
    courts: [],
    todayLabel: ''
  },

  onShow() {
    const today = new Date()
    this.setData({ todayLabel: fmt.fmtDate(today) + ' ' + fmt.weekLabel(today) })
    this.load()
  },

  onPullDownRefresh() {
    this.load().then(function () {
      wx.stopPullDownRefresh()
    })
  },

  load() {
    const self = this
    return Promise.all([api.getVenue(), api.getMerchantOverview(), api.getRevenueSeries()]).then(function (res) {
      const overview = res[1]
      const revenue = res[2]
      self.setData({
        venue: res[0],
        overview: Object.assign({}, overview, {
          revenueText: fmt.fmtMoney(overview.revenue),
          utilizationText: overview.utilization + '%'
        }),
        revenue: revenue,
        courts: overview.courts,
        tasks: overview.tasks.map(function (item) {
          return Object.assign({}, item, {
            statusText:
              item.status === 'done'
                ? '已完成'
                : item.status === 'analyzing'
                ? '分析中 ' + item.progress + '%'
                : item.status === 'queued'
                ? '排队中'
                : '待分析',
            statusClass: item.status === 'done' ? 'ok' : item.status === 'analyzing' ? 'warn' : 'info',
            createdAtLabel: fmt.fmtDateTime(item.createdAt)
          })
        })
      })
    })
  },

  startTask(e) {
    const id = e.currentTarget.dataset.id
    const self = this
    const index = this.data.tasks.findIndex(function (t) {
      return t.id === id
    })
    if (index < 0) return
    if (this.data.tasks[index].status === 'done') {
      wx.showToast({ title: '该任务已完成', icon: 'none' })
      return
    }
    if (this._running && this._running[id]) return
    this._running = this._running || {}
    this._running[id] = true

    const key = 'tasks[' + index + ']'
    let progress = this.data.tasks[index].progress || 0
    wx.showLoading({ title: '正在分析' })
    const timer = setInterval(function () {
      progress += Math.random() * 12 + 5
      if (progress >= 100) {
        progress = 100
        clearInterval(timer)
        wx.hideLoading()
        self._running[id] = false
        const patch = {}
        patch[key + '.status'] = 'done'
        patch[key + '.progress'] = 100
        patch[key + '.statusText'] = '已完成'
        patch[key + '.statusClass'] = 'ok'
        self.setData(patch)
        api.updateAnalysisTask(id, { status: 'done', progress: 100, stage: '已完成' })
        wx.showToast({ title: '分析完成', icon: 'none' })
        return
      }
      const patch = {}
      patch[key + '.status'] = 'analyzing'
      patch[key + '.progress'] = Math.round(progress)
      patch[key + '.statusText'] = '分析中 ' + Math.round(progress) + '%'
      patch[key + '.statusClass'] = 'warn'
      self.setData(patch)
    }, 400)
  },

  goCourts() {
    wx.redirectTo({ url: '/pages/merchant/courts/courts' })
  },

  goManage() {
    wx.redirectTo({ url: '/pages/merchant/manage/manage' })
  },

  goWorkbench() {},

  goCustomer() {
    wx.switchTab({ url: '/pages/home/home' })
  },

  callVenue() {
    wx.makePhoneCall({
      phoneNumber: String(this.data.venue.phone).replace(/[^0-9]/g, ''),
      fail() {}
    })
  }
})
