const api = require('../../utils/api')
const fmt = require('../../utils/format')

const FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'pending', label: '待支付' },
  { key: 'paid', label: '已支付' },
  { key: 'finished', label: '已完成' },
  { key: 'canceled', label: '已取消' }
]

const STATUS_TEXT = {
  pending: '待支付',
  paid: '已支付',
  checked: '已开台',
  finished: '已完成',
  canceled: '已取消',
  refunded: '已退款'
}

const STATUS_CLASS = {
  pending: 'warn',
  paid: 'ok',
  checked: 'info',
  finished: 'ok',
  canceled: '',
  refunded: ''
}

Page({
  data: {
    filters: FILTERS,
    active: 'all',
    list: [],
    counts: {},
    loading: true
  },

  onLoad(options) {
    if (options && options.status) {
      this.setData({ active: options.status })
    }
  },

  onShow() {
    this.load()
  },

  load() {
    const self = this
    return api.getBookings('mine').then(function (bookings) {
      const counts = { all: bookings.length, pending: 0, paid: 0, finished: 0, canceled: 0 }
      bookings.forEach(function (b) {
        if (b.status === 'pending') counts.pending += 1
        else if (b.status === 'paid' || b.status === 'checked') counts.paid += 1
        else if (b.status === 'finished') counts.finished += 1
        else counts.canceled += 1
      })
      const list = bookings.map(function (b) {
        const todayKey = fmt.dateKey(new Date())
        return Object.assign({}, b, {
          statusText: STATUS_TEXT[b.status] || b.status,
          statusClass: STATUS_CLASS[b.status] || '',
          amountText: fmt.fmtMoney(b.amount),
          dateLabel: fmt.fmtDateCn(b.date),
          relative: fmt.relativeDay(b.date),
          weekLabel: fmt.weekLabel(b.date),
          isUpcoming: b.date >= todayKey,
          canPay: b.status === 'pending',
          canCancel: (b.status === 'pending' || b.status === 'paid') && b.date >= todayKey,
          canFinish: b.status === 'paid' || b.status === 'checked',
          canRebook: b.status === 'finished' || b.status === 'canceled'
        })
      })
      self.setData({ list: list, counts: counts, loading: false })
    })
  },

  onFilterTap(e) {
    this.setData({ active: e.currentTarget.dataset.key })
  },

  pay(e) {
    this.act(e.currentTarget.dataset.id, 'pay', '支付成功')
  },

  cancel(e) {
    const self = this
    wx.showModal({
      title: '取消预约',
      content: '开赛前 2 小时可免费取消，确认取消该场地预约？',
      confirmText: '确认取消',
      cancelText: '再想想',
      success(res) {
        if (res.confirm) self.act(e.currentTarget.dataset.id, 'cancel', '已取消')
      }
    })
  },

  finish(e) {
    this.act(e.currentTarget.dataset.id, 'finish', '已标记完成')
  },

  act(id, action, tip) {
    const self = this
    api.updateBooking(id, action).then(function () {
      wx.showToast({ title: tip, icon: 'none' })
      self.load()
    })
  },

  openReport(e) {
    const id = e.currentTarget.dataset.report
    if (!id) {
      wx.showToast({ title: '该场次未开启智能分析', icon: 'none' })
      return
    }
    wx.navigateTo({ url: '/pages/report/report?id=' + id })
  },

  goBooking() {
    wx.switchTab({ url: '/pages/booking/booking' })
  }
})
