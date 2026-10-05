// utils/api.js
// ===========================================================================
// 数据访问层：页面只依赖这里导出的方法，不直接碰 mock 数据。
//
// 接真后端时按下面两步即可（无需改动任何页面）：
//   1) 把 USE_MOCK 改成 false，填 BASE_URL；
//   2) 保证后端返回的字段与本文件注释里标注的结构一致。
//
// 后端契约（与桌面端 WFBARNet 对接时，直接复用它的导出逻辑）：
//   GET  /api/wfbar/v1/user/profile                 -> 用户资料
//   GET  /api/wfbar/v1/venue                        -> 场馆信息
//   GET  /api/wfbar/v1/rallies?status=&limit=        -> 回合记录列表（export_record 结构）
//   GET  /api/wfbar/v1/rallies/{id}                 -> 单个回合原始记录
//   GET  /api/wfbar/v1/rallies/{id}/report          -> 报告数据
//          （= user_report.build_user_report_data_from_rally_record 的输出 JSON）
//   POST /api/wfbar/v1/analysis/tasks               -> 提交视频分析任务 { fileId, options }
//   GET  /api/wfbar/v1/analysis/tasks/{id}          -> 任务进度 { status, progress, stage }
//   GET  /api/wfbar/v1/courts                       -> 场地列表
//   GET  /api/wfbar/v1/courts/schedule?date=        -> 某日排期
//   POST /api/wfbar/v1/bookings                     -> 创建预约
//   GET  /api/wfbar/v1/bookings?status=             -> 预约列表
//   POST /api/wfbar/v1/bookings/{id}/{action}       -> pay | cancel | checkin | finish
//   GET  /api/wfbar/v1/merchant/overview            -> 商家工作台概览
//   PATCH /api/wfbar/v1/merchant/courts/{id}        -> 场地开放状态 / 价格
// ===========================================================================

const engine = require('./engine/report-engine')
const mock = require('./engine/mock-records')
const aggregate = require('./engine/aggregate')
const fmt = require('./format')

const USE_MOCK = true
const BASE_URL = 'https://api.example.com'
const STATE_KEY = 'wfbar_mock_state'

// --------------------------------------------------------------------------
// 场馆与场地
// --------------------------------------------------------------------------
const VENUE = {
  id: 'venue-jiangda-01',
  name: '羽动星空智慧球馆',
  branch: '江大店',
  address: '江苏省镇江市京口区学府路 301 号体育中心 3 层',
  phone: '0511-8888 6120',
  openTime: '09:00',
  closeTime: '22:30',
  notice: '开赛前 2 小时可免费取消；迟到 15 分钟视为自动放弃。场地含智能机位，可自动生成分析报告。',
  tags: ['6 片标准场地', '全场机位覆盖', 'WFBARNet 智能分析', '免费停车 2 小时'],
  rating: 4.9,
  reviewCount: 1286
}

const BASE_COURTS = [
  { id: 'c1', name: '1 号场', type: '标准双打', surface: 'PVC 运动地胶', price: 60, open: true, maintenance: false, camera: true },
  { id: 'c2', name: '2 号场', type: '标准双打', surface: 'PVC 运动地胶', price: 60, open: true, maintenance: false, camera: true },
  { id: 'c3', name: '3 号场', type: '标准双打', surface: '木地板', price: 55, open: true, maintenance: false, camera: true },
  { id: 'c4', name: '4 号场', type: '标准双打', surface: '木地板', price: 55, open: true, maintenance: false, camera: false },
  { id: 'c5', name: '5 号场', type: '训练场', surface: 'PVC 运动地胶', price: 48, open: true, maintenance: false, camera: true },
  { id: 'c6', name: '6 号场', type: '训练场', surface: 'PVC 运动地胶', price: 48, open: false, maintenance: true, camera: false }
]

const USER = {
  id: 'u-10086',
  name: '林知遥',
  avatarText: '林',
  phone: '138****6620',
  level: '业余高阶',
  memberLevel: '黄金会员',
  balance: 486.5,
  points: 1280,
  joinedAt: '2026-03-12',
  coach: '吴教练',
  favoriteVenue: '羽动星空智慧球馆 · 江大店'
}

const SLOT_START = 9 * 60
const SLOT_END = 22 * 60
const SLOT_MINUTES = 60

// --------------------------------------------------------------------------
// 本地可变状态（模拟服务端写操作）
// --------------------------------------------------------------------------
function state() {
  let s = wx.getStorageSync(STATE_KEY)
  if (!s || typeof s !== 'object') {
    s = { bookings: seededBookings(), courts: null, tasks: null }
  }
  if (!s.courts) s.courts = clone(BASE_COURTS)
  if (!s.tasks) {
    s.tasks = mock.PENDING.map(function (item) {
      return { id: item.id, videoName: item.rally_name, status: item.status, progress: item.progress || 0, stage: item.stage || '排队中', courtName: item.courtName, customer: item.athleteName, createdAt: item.createdAt }
    })
  }
  return s
}

function saveState(s) {
  wx.setStorageSync(STATE_KEY, s)
}

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function seededBookings() {
  const rng = mock.makeRandom(8801)
  const list = []
  let seq = 1
  for (let day = 0; day < 7; day += 1) {
    for (let ci = 0; ci < BASE_COURTS.length; ci += 1) {
      const court = BASE_COURTS[ci]
      if (!court.open) continue
      for (let slot = 0; slot < 13; slot += 1) {
        const r = rng()
        const hour = 9 + slot
        const prime = hour >= 18 && hour <= 21
        const threshold = prime ? 0.62 : day === 0 ? 0.5 : 0.34
        if (r > threshold) continue
        const startMinutes = hour * 60
        const duration = rng() < 0.18 ? 120 : 60
        const name = mock.guestName(9000 + seq * 137)
        list.push({
          id: 'bk-' + fmt.pad2(seq),
          code: 'WF' + (20260920 + seq),
          courtId: court.id,
          courtName: court.name,
          date: fmt.dateKey(new Date(Date.now() + day * 86400000)),
          dayOffset: day,
          startMinutes: startMinutes,
          endMinutes: startMinutes + duration,
          startLabel: fmt.minuteToClock(startMinutes),
          endLabel: fmt.minuteToClock(startMinutes + duration),
          durationMinutes: duration,
          amount: Math.round((court.price * duration) / 60),
          customer: name,
          phone: '1' + (30 + Math.floor(rng() * 60)) + '****' + Math.floor(1000 + rng() * 8999),
          channel: rng() < 0.45 ? '小程序自助' : '到店前台',
          status: day === 0 && hour < new Date().getHours() ? 'finished' : rng() < 0.12 ? 'pending' : 'paid',
          withAnalysis: rng() < 0.55
        })
        seq += 1
      }
    }
  }
  // 顾客自己的预约（列表里带 mine 标记）
  list.push({
    id: 'bk-mine-01',
    code: 'WF20261012',
    courtId: 'c1',
    courtName: '1 号场',
    date: fmt.dateKey(new Date(Date.now() + 86400000)),
    dayOffset: 1,
    startMinutes: 19 * 60,
    endMinutes: 20 * 60,
    startLabel: '19:00',
    endLabel: '20:00',
    durationMinutes: 60,
    amount: 60,
    customer: USER.name,
    phone: USER.phone,
    channel: '小程序自助',
    status: 'paid',
    mine: true,
    withAnalysis: true,
    coach: '吴教练'
  })
  list.push({
    id: 'bk-mine-02',
    code: 'WF20261005',
    courtId: 'c5',
    courtName: '5 号场',
    date: fmt.dateKey(new Date(Date.now() - 2 * 86400000)),
    dayOffset: -2,
    startMinutes: 20 * 60,
    endMinutes: 21 * 60,
    startLabel: '20:00',
    endLabel: '21:00',
    durationMinutes: 60,
    amount: 48,
    customer: USER.name,
    phone: USER.phone,
    channel: '小程序自助',
    status: 'finished',
    mine: true,
    withAnalysis: true,
    reportId: 'rec-08',
    coach: '吴教练'
  })
  list.push({
    id: 'bk-mine-03',
    code: 'WF20260928',
    courtId: 'c2',
    courtName: '2 号场',
    date: fmt.dateKey(new Date(Date.now() - 6 * 86400000)),
    dayOffset: -6,
    startMinutes: 18 * 60,
    endMinutes: 19 * 60,
    startLabel: '18:00',
    endLabel: '19:00',
    durationMinutes: 60,
    amount: 60,
    customer: USER.name,
    phone: USER.phone,
    channel: '小程序自助',
    status: 'finished',
    mine: true,
    withAnalysis: true,
    reportId: 'rec-07',
    coach: '吴教练'
  })
  list.push({
    id: 'bk-mine-04',
    code: 'WF20260915',
    courtId: 'c3',
    courtName: '3 号场',
    date: fmt.dateKey(new Date(Date.now() - 5 * 86400000)),
    dayOffset: -5,
    startMinutes: 17 * 60,
    endMinutes: 18 * 60,
    startLabel: '17:00',
    endLabel: '18:00',
    durationMinutes: 60,
    amount: 55,
    customer: USER.name,
    phone: USER.phone,
    channel: '小程序自助',
    status: 'canceled',
    mine: true,
    withAnalysis: false
  })
  return clone(list)
}

// --------------------------------------------------------------------------
// 异步封装
// --------------------------------------------------------------------------
function delay(payload, ms) {
  return new Promise(function (resolve) {
    setTimeout(function () {
      resolve(clone(payload))
    }, ms || 200)
  })
}

function http(path, method, data) {
  if (USE_MOCK) return Promise.reject(new Error('mock mode'))
  return new Promise(function (resolve, reject) {
    wx.request({
      url: BASE_URL + path,
      method: method || 'GET',
      data: data || {},
      header: { 'content-type': 'application/json' },
      success: function (res) {
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(res.data)
        else reject(new Error('HTTP ' + res.statusCode))
      },
      fail: reject
    })
  })
}

// --------------------------------------------------------------------------
// 对外 API
// --------------------------------------------------------------------------
const api = {
  isMock: function () { return USE_MOCK },

  getUserProfile: function () {
    return delay(USER, 120)
  },

  getVenue: function () {
    return delay(VENUE, 120)
  },

  getCourts: function () {
    return delay(state().courts, 160)
  },

  /** 训练记录列表（含待分析任务） */
  getTrainingRecords: function (filter) {
    const s = state()
    const list = []
    mock.RECORDS.forEach(function (record) {
      const summary = engine.summarize(record, { athleteName: record.athleteName })
      list.push({
        id: record.id,
        videoName: record.rally_name,
        createdAt: record.createdAt,
        dateLabel: fmt.fmtDate(record.createdAt),
        dayLabel: fmt.fmtDateCn(record.createdAt),
        relative: fmt.relativeDay(record.createdAt),
        courtName: record.courtName,
        sessionKind: record.sessionKind,
        coachName: record.coachName,
        status: 'done',
        score: summary.overall_score,
        levelLabel: summary.level_label,
        hitCount: summary.hit_count,
        durationLabel: fmt.fmtSeconds(summary.duration_s),
        distanceM: summary.distance_m,
        maxBallKmh: summary.max_ball_kmh,
        weakest: summary.weakest,
        strongest: summary.strongest
      })
    })
    s.tasks.forEach(function (task) {
      list.push({
        id: task.id,
        videoName: task.videoName,
        createdAt: task.createdAt,
        dateLabel: fmt.fmtDate(task.createdAt),
        dayLabel: fmt.fmtDateCn(task.createdAt),
        relative: fmt.relativeDay(task.createdAt),
        courtName: task.courtName,
        sessionKind: '客户上传',
        coachName: '待分配',
        status: task.status,
        progress: task.progress,
        stage: task.stage,
        score: 0
      })
    })
    list.sort(function (a, b) {
      return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
    })
    let result = list
    if (filter && filter !== 'all') {
      result = list.filter(function (item) {
        if (filter === 'done') return item.status === 'done'
        if (filter === 'pending') return item.status !== 'done'
        return true
      })
    }
    return delay(result, 200)
  },

  /** 单份报告的完整视图数据（与桌面端报告字段一一对应） */
  getTrainingReport: function (id) {
    const record = mock.RECORDS.filter(function (item) { return item.id === id })[0]
    if (!record) return Promise.reject(new Error('记录不存在：' + id))
    const report = engine.buildReportData(record, {
      athleteName: record.athleteName,
      videoName: record.rally_name,
      generatedAt: fmt.fmtDateTime(record.createdAt),
      userPlayer: 'bottom'
    })
    report.meta.court_name = record.courtName
    report.meta.session_kind = record.sessionKind
    report.meta.coach_name = record.coachName
    report.meta.record_id = record.id
    return delay(report, 220)
  },

  /** 整体表现聚合 */
  getPerformance: function (range) {
    const perf = aggregate.buildPerformance(mock.RECORDS, { range: range || 'all' })
    return delay(perf, 240)
  },

  /** 提交视频分析任务 */
  createAnalysisTask: function (payload) {
    const s = state()
    const id = 'rec-p' + (s.tasks.length + 1)
    const task = {
      id: id,
      videoName: (payload && payload.fileName) || 'new_upload.mp4',
      status: 'queued',
      progress: 0,
      stage: '排队中',
      courtName: (payload && payload.courtName) || '2 号场',
      customer: USER.name,
      createdAt: new Date().toISOString()
    }
    s.tasks.unshift(task)
    saveState(s)
    return delay(task, 260)
  },

  getAnalysisTasks: function () {
    return delay(state().tasks, 180)
  },

  updateAnalysisTask: function (id, patch) {
    const s = state()
    s.tasks = s.tasks.map(function (item) {
      if (item.id !== id) return item
      return Object.assign({}, item, patch)
    })
    saveState(s)
    return delay(true, 80)
  },

  /** 某日排期矩阵 */
  getSchedule: function (dateKey) {
    const s = state()
    const courts = s.courts.filter(function (c) { return c.open && !c.maintenance })
    const bookings = s.bookings.filter(function (b) {
      return b.date === dateKey && b.status !== 'canceled'
    })
    const slots = []
    for (let m = SLOT_START; m + SLOT_MINUTES <= SLOT_END; m += SLOT_MINUTES) {
      slots.push(m)
    }
    const grid = slots.map(function (start) {
      const row = { startMinutes: start, label: fmt.minuteToClock(start), cells: [] }
      courts.forEach(function (court) {
        const hit = bookings.filter(function (b) {
          return b.courtId === court.id && b.startMinutes < start + SLOT_MINUTES && b.endMinutes > start
        })[0]
        row.cells.push({
          courtId: court.id,
          courtName: court.name,
          price: court.price,
          available: !hit,
          bookingId: hit ? hit.id : '',
          customer: hit ? hit.customer : '',
          status: hit ? hit.status : ''
        })
      })
      return row
    })
    return delay({ date: dateKey, courts: courts, slots: slots, grid: grid, bookings: bookings }, 200)
  },

  createBooking: function (payload) {
    const s = state()
    const court = s.courts.filter(function (c) { return c.id === payload.courtId })[0] || s.courts[0]
    const start = payload.startMinutes
    const duration = payload.durationMinutes || 60
    const booking = {
      id: 'bk-' + Date.now(),
      code: 'WF' + String(Date.now()).slice(-8),
      courtId: court.id,
      courtName: court.name,
      date: payload.date,
      dayOffset: 0,
      startMinutes: start,
      endMinutes: start + duration,
      startLabel: fmt.minuteToClock(start),
      endLabel: fmt.minuteToClock(start + duration),
      durationMinutes: duration,
      amount: Math.round((court.price * duration) / 60),
      customer: USER.name,
      phone: USER.phone,
      channel: '小程序自助',
      status: 'pending',
      mine: true,
      withAnalysis: !!payload.withAnalysis,
      coach: payload.coach || '',
      remark: payload.remark || ''
    }
    s.bookings.push(booking)
    saveState(s)
    return delay(booking, 320)
  },

  getBookings: function (scope) {
    const s = state()
    let list = s.bookings.slice()
    if (scope === 'mine') {
      list = list.filter(function (b) { return b.mine })
    }
    list.sort(function (a, b) {
      const da = a.date + fmt.pad2(Math.floor(a.startMinutes / 60))
      const db = b.date + fmt.pad2(Math.floor(b.startMinutes / 60))
      return db < da ? -1 : db > da ? 1 : 0
    })
    return delay(list, 200)
  },

  updateBooking: function (id, action) {
    const s = state()
    const map = { pay: 'paid', cancel: 'canceled', checkin: 'checked', finish: 'finished', refund: 'refunded' }
    s.bookings = s.bookings.map(function (item) {
      if (item.id !== id) return item
      const next = Object.assign({}, item, { status: map[action] || item.status })
      if (action === 'pay') next.paidAt = new Date().toISOString()
      return next
    })
    saveState(s)
    return delay(true, 220)
  },

  // ---------------- 商家端 ----------------
  getMerchantOverview: function () {
    const s = state()
    const today = fmt.dateKey(new Date())
    const todays = s.bookings.filter(function (b) {
      return b.date === today && b.status !== 'canceled'
    })
    const revenue = todays.reduce(function (a, b) { return a + b.amount }, 0)
    const closedCourts = s.courts.filter(function (c) { return !c.open || c.maintenance })
    const availableCourts = s.courts.length - closedCourts.length
    const usedSlots = todays.reduce(function (a, b) { return a + b.durationMinutes / 60 }, 0)
    const capacity = availableCourts * ((SLOT_END - SLOT_START) / 60)
    const now = new Date()
    const nowMinutes = now.getHours() * 60 + now.getMinutes()
    const onCourt = todays.filter(function (b) {
      return b.startMinutes <= nowMinutes && b.endMinutes > nowMinutes
    }).length
    return delay(
      {
        date: today,
        bookingCount: todays.length,
        revenue: revenue,
        onCourt: onCourt,
        utilization: Math.round((usedSlots / Math.max(1, capacity)) * 100),
        availableCourts: availableCourts,
        totalCourts: s.courts.length,
        pendingCheckin: todays.filter(function (b) { return b.status === 'pending' }).length,
        analysisQueue: s.tasks.filter(function (t) { return t.status !== 'done' }).length,
        courts: s.courts.map(function (court) {
          const current = todays.filter(function (b) {
            return b.courtId === court.id && b.startMinutes <= nowMinutes && b.endMinutes > nowMinutes
          })[0]
          const next = todays
            .filter(function (b) { return b.courtId === court.id && b.startMinutes > nowMinutes })
            .sort(function (a, b) { return a.startMinutes - b.startMinutes })[0]
          return {
            id: court.id,
            name: court.name,
            type: court.type,
            price: court.price,
            open: court.open,
            maintenance: court.maintenance,
            state: court.maintenance ? '维护中' : !court.open ? '已停用' : current ? '使用中' : '空闲中',
            stateClass: court.maintenance || !court.open ? 'off' : current ? 'busy' : 'idle',
            currentCustomer: current ? current.customer : '',
            nextLabel: next ? next.startLabel + ' ' + next.customer : '今日无后续预约',
            todayCount: todays.filter(function (b) { return b.courtId === court.id }).length
          }
        }),
        tasks: s.tasks
      },
      220
    )
  },

  updateCourt: function (courtId, patch) {
    const s = state()
    s.courts = s.courts.map(function (court) {
      if (court.id !== courtId) return court
      return Object.assign({}, court, patch)
    })
    saveState(s)
    return delay(true, 180)
  },

  addCourt: function (payload) {
    const s = state()
    const id = 'c' + (s.courts.length + 1)
    s.courts.push({
      id: id,
      name: payload.name,
      type: payload.type || '标准双打',
      surface: payload.surface || 'PVC 运动地胶',
      price: payload.price || 55,
      open: true,
      maintenance: false,
      camera: !!payload.camera
    })
    saveState(s)
    return delay(true, 220)
  },

  getMerchantBookings: function (filter) {
    const s = state()
    let list = s.bookings.slice()
    if (filter && filter.date) {
      list = list.filter(function (b) { return b.date === filter.date })
    }
    if (filter && filter.status && filter.status !== 'all') {
      list = list.filter(function (b) { return b.status === filter.status })
    }
    list.sort(function (a, b) {
      return a.date + fmt.pad2(Math.floor(a.startMinutes / 60)) < b.date + fmt.pad2(Math.floor(b.startMinutes / 60)) ? -1 : 1
    })
    return delay(list, 200)
  },

  getRevenueSeries: function () {
    const s = state()
    const series = []
    for (let i = 13; i >= 0; i -= 1) {
      const d = new Date(Date.now() - i * 86400000)
      const key = fmt.dateKey(d)
      const amount = s.bookings
        .filter(function (b) { return b.date === key && b.status !== 'canceled' })
        .reduce(function (a, b) { return a + b.amount }, 0)
      series.push({ label: fmt.fmtDateCn(d), value: amount })
    }
    return delay(series, 200)
  }
}

module.exports = api
module.exports.VENUE = VENUE
module.exports.USER = USER
module.exports.SLOT_START = SLOT_START
module.exports.SLOT_END = SLOT_END
module.exports.SLOT_MINUTES = SLOT_MINUTES
