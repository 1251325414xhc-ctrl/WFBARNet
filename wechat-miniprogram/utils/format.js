// utils/format.js —— 展示层格式化工具（与桌面端 user_report.py 的 _fmt_* 保持一致）

function pad2(value) {
  return value < 10 ? '0' + value : String(value)
}

function num(value) {
  const n = Number(value)
  return isFinite(n) ? n : 0
}

/** 「138.0秒」→「2分18秒」，与桌面端 _fmt_seconds 一致 */
function fmtSeconds(value) {
  const seconds = num(value)
  if (seconds >= 60) {
    const minutes = Math.floor(seconds / 60)
    const rest = Math.round(seconds - minutes * 60)
    return minutes + '分' + pad2(rest) + '秒'
  }
  return seconds.toFixed(1) + '秒'
}

/** 毫秒 → m:ss.xx，与桌面端 _fmt_time_ms 一致 */
function fmtTimeMs(value) {
  const seconds = Math.max(0, num(value) / 1000)
  const minutes = Math.floor(seconds / 60)
  const rest = seconds - minutes * 60
  if (minutes) {
    return minutes + ':' + (rest < 10 ? '0' : '') + rest.toFixed(2)
  }
  return rest.toFixed(2) + 's'
}

function fmtPercent(ratio, digits) {
  const d = typeof digits === 'number' ? digits : 1
  return (num(ratio) * 100).toFixed(d) + '%'
}

function fmtMeters(value) {
  return num(value).toFixed(1) + ' m'
}

function fmtSpeed(value) {
  return num(value).toFixed(2) + ' m/s'
}

function fmtMoney(value) {
  const n = num(value)
  return '¥' + (n % 1 === 0 ? String(n) : n.toFixed(2))
}

function dateKey(d) {
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate())
}

function fmtDate(value) {
  const d = value instanceof Date ? value : new Date(value)
  if (isNaN(d.getTime())) return ''
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate())
}

function fmtDateCn(value) {
  const d = value instanceof Date ? value : new Date(value)
  if (isNaN(d.getTime())) return ''
  return (d.getMonth() + 1) + '月' + d.getDate() + '日'
}

function fmtDateTime(value) {
  const d = value instanceof Date ? value : new Date(value)
  if (isNaN(d.getTime())) return ''
  return fmtDate(d) + ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes())
}

const WEEK_LABELS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

function weekLabel(value) {
  const d = value instanceof Date ? value : new Date(value)
  return WEEK_LABELS[d.getDay()]
}

/** 相对今天的中文描述 */
function relativeDay(value) {
  const d = value instanceof Date ? value : new Date(value)
  const today = new Date()
  const a = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const b = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()
  const diff = Math.round((b - a) / 86400000)
  if (diff === 0) return '今天'
  if (diff === 1) return '昨天'
  if (diff === 2) return '前天'
  if (diff > 0) return diff + ' 天前'
  if (diff === -1) return '明天'
  return Math.abs(diff) + ' 天后'
}

function minuteToClock(minutes) {
  const h = Math.floor(num(minutes) / 60)
  const m = Math.round(num(minutes) % 60)
  return pad2(h) + ':' + pad2(m)
}

function addMinutes(base, minutes) {
  return new Date(base.getTime() + num(minutes) * 60000)
}

module.exports = {
  pad2: pad2,
  num: num,
  fmtSeconds: fmtSeconds,
  fmtTimeMs: fmtTimeMs,
  fmtPercent: fmtPercent,
  fmtMeters: fmtMeters,
  fmtSpeed: fmtSpeed,
  fmtMoney: fmtMoney,
  fmtDate: fmtDate,
  fmtDateCn: fmtDateCn,
  fmtDateTime: fmtDateTime,
  dateKey: dateKey,
  weekLabel: weekLabel,
  relativeDay: relativeDay,
  minuteToClock: minuteToClock,
  addMinutes: addMinutes,
  WEEK_LABELS: WEEK_LABELS
}
