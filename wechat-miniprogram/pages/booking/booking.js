const api = require('../../utils/api')
const fmt = require('../../utils/format')

const DURATIONS = [
  { value: 60, label: '1 小时' },
  { value: 90, label: '1.5 小时' },
  { value: 120, label: '2 小时' }
]

function buildDates() {
  const out = []
  for (let i = 0; i < 7; i += 1) {
    const d = new Date(Date.now() + i * 86400000)
    out.push({
      key: fmt.dateKey(d),
      day: d.getDate(),
      week: i === 0 ? '今天' : i === 1 ? '明天' : fmt.weekLabel(d),
      month: d.getMonth() + 1
    })
  }
  return out
}

Page({
  data: {
    dates: buildDates(),
    activeDate: '',
    durations: DURATIONS,
    duration: 60,
    loading: true,
    schedule: null,
    selected: null,
    showSheet: false,
    withAnalysis: true,
    coach: '',
    remark: '',
    summary: null,
    myUpcoming: null
  },

  onLoad() {
    const dates = buildDates()
    this.setData({ dates: dates, activeDate: dates[0].key })
  },

  onShow() {
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 2 })
    }
    this.loadSchedule(this.data.activeDate)
    this.loadMine()
  },

  loadMine() {
    const self = this
    api.getBookings('mine').then(function (list) {
      const todayKey = fmt.dateKey(new Date())
      const next = list
        .filter(function (b) {
          return b.date >= todayKey && (b.status === 'paid' || b.status === 'pending')
        })
        .sort(function (a, b) {
          return a.date < b.date ? -1 : 1
        })[0]
      self.setData({
        myUpcoming: next
          ? Object.assign({}, next, {
              dateLabel: fmt.fmtDateCn(next.date),
              relative: fmt.relativeDay(next.date)
            })
          : null
      })
    })
  },

  loadSchedule(dateKey) {
    const self = this
    self.setData({ loading: true })
    return api.getSchedule(dateKey).then(function (schedule) {
      const rows = schedule.bookings.length
        ? schedule.grid.map(function (row) {
            const cells = row.cells.map(function (cell) {
              return Object.assign({}, cell, {
                statusText: cell.available ? '可约' : '已约',
                customerText: cell.available ? '' : cell.customer.slice(0, 1) + '先生/女士'
              })
            })
            const free = cells.filter(function (c) {
              return c.available
            })
            return {
              startMinutes: row.startMinutes,
              label: row.label,
              endLabel: fmt.minuteToClock(row.startMinutes + 60),
              cells: cells,
              freeCount: free.length,
              prime: row.startMinutes >= 18 * 60
            }
          })
        : []
      self.setData({
        schedule: {
          courts: schedule.courts,
          rows: rows,
          date: dateKey,
          totalFree: rows.reduce(function (a, r) {
            return a + r.freeCount
          }, 0)
        },
        loading: false
      })
      self.recompute()
    })
  },

  onDateTap(e) {
    const key = e.currentTarget.dataset.key
    this.setData({ activeDate: key, selected: null })
    this.loadSchedule(key)
  },

  onDurationTap(e) {
    this.setData({ duration: Number(e.currentTarget.dataset.value) })
    this.recompute()
  },

  onCellTap(e) {
    const row = Number(e.currentTarget.dataset.row)
    const cell = Number(e.currentTarget.dataset.cell)
    const schedule = this.data.schedule
    if (!schedule) return
    const target = schedule.rows[row].cells[cell]
    if (!target.available) {
      wx.showToast({ title: '该时段已被预约', icon: 'none' })
      return
    }
    this.setData({
      selected: {
        courtId: target.courtId,
        courtName: target.courtName,
        price: target.price,
        startMinutes: schedule.rows[row].startMinutes
      }
    })
    this.recompute()
  },

  /** 校验所选时长内是否全部空闲，并算出金额 */
  recompute() {
    const selected = this.data.selected
    const schedule = this.data.schedule
    if (!selected || !schedule) {
      this.setData({ summary: null })
      return
    }
    const need = Math.ceil(this.data.duration / 60)
    const startIndex = schedule.rows.findIndex(function (row) {
      return row.startMinutes === selected.startMinutes
    })
    let conflict = ''
    for (let i = 0; i < need; i += 1) {
      const row = schedule.rows[startIndex + i]
      if (!row) {
        conflict = '超出营业时间'
        break
      }
      const cell = row.cells.filter(function (c) {
        return c.courtId === selected.courtId
      })[0]
      if (!cell || !cell.available) {
        conflict = row.label + ' 已被占用'
        break
      }
    }
    const endMinutes = selected.startMinutes + need * 60
    this.setData({
      summary: {
        courtName: selected.courtName,
        dateLabel: fmt.fmtDateCn(schedule.date),
        relative: fmt.relativeDay(schedule.date),
        week: fmt.weekLabel(schedule.date),
        startLabel: fmt.minuteToClock(selected.startMinutes),
        endLabel: fmt.minuteToClock(endMinutes),
        duration: this.data.duration,
        amount: Math.round((selected.price * this.data.duration) / 60),
        conflict: conflict
      }
    })
  },

  openSheet() {
    if (!this.data.summary) {
      wx.showToast({ title: '请先在时间表里选一个时段', icon: 'none' })
      return
    }
    if (this.data.summary.conflict) {
      wx.showToast({ title: this.data.summary.conflict, icon: 'none' })
      return
    }
    this.setData({ showSheet: true })
  },

  closeSheet() {
    this.setData({ showSheet: false })
  },

  toggleAnalysis(e) {
    this.setData({ withAnalysis: e.detail.value })
  },

  onRemarkInput(e) {
    this.setData({ remark: e.detail.value })
  },

  confirmBooking() {
    const self = this
    const selected = this.data.selected
    const summary = this.data.summary
    api
      .createBooking({
        courtId: selected.courtId,
        date: this.data.schedule.date,
        startMinutes: selected.startMinutes,
        durationMinutes: this.data.duration,
        withAnalysis: this.data.withAnalysis,
        remark: this.data.remark
      })
      .then(function (booking) {
        self.setData({ showSheet: false, selected: null, summary: null })
        wx.showModal({
          title: '预约成功',
          content:
            summary.dateLabel + ' ' + summary.startLabel + '-' + summary.endLabel + ' ' +
            summary.courtName + '，金额 ' + fmt.fmtMoney(summary.amount) +
            '。请到「我的预约」完成支付。',
          confirmText: '去支付',
          cancelText: '稍后',
          success(res) {
            if (res.confirm) {
              wx.navigateTo({ url: '/pages/orders/orders?status=pending' })
            } else {
              self.loadSchedule(self.data.activeDate)
              self.loadMine()
            }
          }
        })
      })
  },

  goOrders() {
    wx.navigateTo({ url: '/pages/orders/orders' })
  }
})
