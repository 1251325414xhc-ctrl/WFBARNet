// app.js —— 羽迹智能小程序入口
// 远端数据统一走 utils/api.js（当前为 mock 通道，切换真后端只需改 api.js 顶部的 USE_MOCK）
const api = require('./utils/api')

App({
  globalData: {
    role: 'customer', // customer | merchant
    user: null,
    venue: null,
    lastReportId: 'rec-01'
  },

  onLaunch() {
    this.readyPromise = this._bootstrap()
  },

  _bootstrap() {
    return Promise.all([api.getUserProfile(), api.getVenue()]).then(([user, venue]) => {
      this.globalData.user = user
      this.globalData.venue = venue
      return this.globalData
    })
  },

  // 页面里用 app.whenReady().then(...) 保证用户/场馆信息已就绪
  whenReady() {
    if (!this.readyPromise) {
      this.readyPromise = this._bootstrap()
    }
    return this.readyPromise
  },

  setRole(role) {
    this.globalData.role = role
    wx.setStorageSync('wfbar_role', role)
  },

  rememberReport(id) {
    this.globalData.lastReportId = id
    wx.setStorageSync('wfbar_last_report', id)
  },

  lastReportId() {
    return wx.getStorageSync('wfbar_last_report') || this.globalData.lastReportId
  }
})
