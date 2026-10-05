const api = require('../../utils/api')
const fmt = require('../../utils/format')

const FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'done', label: '已完成' },
  { key: 'pending', label: '待分析' }
]

Page({
  data: {
    filters: FILTERS,
    active: 'all',
    list: [],
    counts: { all: 0, done: 0, pending: 0 },
    loading: true
  },

  onShow() {
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 1 })
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
    return api.getTrainingRecords('all').then(function (records) {
      const decorated = records.map(function (item) {
        const done = item.status === 'done'
        return Object.assign({}, item, {
          statusLabel: done
            ? '已完成'
            : item.status === 'queued'
            ? '排队中'
            : item.status === 'analyzing'
            ? '分析中 ' + (item.progress || 0) + '%'
            : '待分析',
          statusClass: done ? 'ok' : 'warn',
          scoreLabel: done ? String(item.score) : '--',
          levelLabel: done ? item.levelLabel : '待生成报告',
          metaLine: item.dayLabel + ' · ' + item.courtName + ' · ' + item.coachName,
          detailLine: done
            ? '有效击球 ' + item.hitCount + ' 次 · 时长 ' + item.durationLabel + ' · 跑动 ' +
              Number(item.distanceM).toFixed(1) + ' m'
            : item.sessionKind,
          weakLine: done ? '短板：' + item.weakest.name + '（' + item.weakest.score + '分）' : ''
        })
      })
      let counts = { all: decorated.length, done: 0, pending: 0 }
      decorated.forEach(function (item) {
        if (item.status === 'done') counts.done += 1
        else counts.pending += 1
      })
      self.setData({ list: decorated, counts: counts, loading: false })
    })
  },

  onFilterTap(e) {
    this.setData({ active: e.currentTarget.dataset.key })
  },

  openReport(e) {
    const id = e.currentTarget.dataset.id
    const status = e.currentTarget.dataset.status
    if (status !== 'done') {
      wx.showToast({ title: '该任务还没有生成报告', icon: 'none' })
      return
    }
    wx.navigateTo({ url: '/pages/report/report?id=' + id })
  },

  goAnalysis() {
    wx.navigateTo({ url: '/pages/analysis/analysis' })
  },

  goPerformance() {
    wx.navigateTo({ url: '/pages/performance/performance' })
  }
})
