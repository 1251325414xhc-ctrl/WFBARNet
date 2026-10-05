const api = require('../../../utils/api')
const fmt = require('../../../utils/format')

const TABS = [
  { key: 'list', label: '预约列表' },
  { key: 'grid', label: '排期视图' }
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

const FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'pending', label: '待支付' },
  { key: 'paid', label: '已支付' },
  { key: 'finished', label: '已完成' }
]

function buildDates() {
  const out = []
  for (let i = 0; i < 7; i += 1) {
    const d = new Date(Date.now() + i * 86400000)
    out.push({
      key: fmt.dateKey(d),
      day: d.getDate(),
      week: i === 0 ? '今天' : i === 1 ? '明天' : fmt.weekLabel(d)
    })
  }
  return out
}

Page({
  data: {
    tabs: TABS,
    active: 'list',
    filters: FILTERS,
    activeFilter: 'all',
    dates: buildDates(),
    activeDate: '',
    bookings: [],
    columns: [],
    stats: null,
    loading: true
  },

  onLoad() {
    const dates = buildDates()
    this.setData({ dates: dates, activeDate: dates[0].key })
  },

  onShow() {
    this.load()
  },

  load() {
    const self = this
    const dateKey = this.data.activeDate || fmt.dateKey(new Date())
    return Promise.all([
      api.getMerchantBookings({ date: dateKey }),
      api.getSchedule(dateKey),
      api.getRevenueSeries()
    ]).then(function (res) {
      const bookings = res[0]
        .filter(function (b) {
          return b.status !== 'canceled'
        })
        .map(function (b) {
          return Object.assign({}, b, {
            statusText: STATUS_TEXT[b.status] || b.status,
            statusClass: STATUS_CLASS[b.status] || '',
            amountText: fmt.fmtMoney(b.amount),
            timeText: b.startLabel + ' - ' + b.endLabel,
            canCheckin: b.status === 'paid',
            canFinish: b.status === 'checked',
            canRefund: b.status === 'paid' || b.status === 'checked'
          })
        })

      // 排期表转置：一行=一小时 → 一列=一片场地
      const schedule = res[1]
      const columns = schedule.courts.map(function (court, courtIndex) {
        const cells = schedule.grid.map(function (row) {
          const cell = row.cells[courtIndex]
          const occupied = !cell.available
          return {
            label: occupied ? cell.customer.slice(0, 1) + '先生/女士' : '空闲',
            state: occupied ? 'busy' : 'free',
            startLabel: row.label
          }
        })
        return { id: court.id, name: court.name, price: court.price, cells: cells }
      })
      const times = schedule.grid.map(function (row) {
        return row.label
      })

      const revenueToday = bookings.reduce(function (a, b) {
        return a + b.amount
      }, 0)
      const openCourts = schedule.courts.length
      const usedHours = bookings.reduce(function (a, b) {
        return a + b.durationMinutes / 60
      }, 0)
      const capacity = Math.max(1, openCourts * schedule.grid.length)

      self.setData({
        bookings: bookings,
        columns: columns,
        times: times,
        stats: {
          count: bookings.length,
          revenueText: fmt.fmtMoney(revenueToday),
          utilization: Math.round((usedHours / capacity) * 100) + '%',
          pending: bookings.filter(function (b) {
            return b.status === 'pending'
          }).length
        },
        loading: false
      })
    })
  },

  onTabTap(e) {
    this.setData({ active: e.currentTarget.dataset.key })
  },

  onFilterTap(e) {
    this.setData({ activeFilter: e.currentTarget.dataset.key })
  },

  onDateTap(e) {
    this.setData({ activeDate: e.currentTarget.dataset.key })
    this.load()
  },

  act(e) {
    const id = e.currentTarget.dataset.id
    const action = e.currentTarget.dataset.action
    const tips = { checkin: '已开台', finish: '已完成', cancel: '已取消', refund: '已发起退款', pay: '已标记收款' }
    const self = this
    api.updateBooking(id, action).then(function () {
      wx.showToast({ title: tips[action] || '操作成功', icon: 'none' })
      self.load()
    })
  },

  callCustomer(e) {
    const phone = e.currentTarget.dataset.phone
    wx.makePhoneCall({
      phoneNumber: String(phone).replace(/[^0-9]/g, ''),
      fail() {}
    })
  },

  goWorkbench() {
    wx.redirectTo({ url: '/pages/merchant/workbench/workbench' })
  },

  goCourts() {
    wx.redirectTo({ url: '/pages/merchant/courts/courts' })
  }
})
