// utils/engine/aggregate.js —— 跨多次训练的聚合分析（整体表现页 + 首页趋势）
const engine = require('./report-engine')
const fmt = require('../format')

function sortByTime(records) {
  return records.slice().sort(function (a, b) {
    return new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()
  })
}

/** 单次记录 → 轻量摘要 */
function toSummaryItem(record) {
  const report = engine.buildReportData(record, {
    athleteName: record.athleteName,
    videoName: record.rally_name,
    generatedAt: fmt.fmtDateTime(record.createdAt)
  })
  return {
    id: record.id,
    videoName: record.rally_name,
    createdAt: record.createdAt,
    dateLabel: fmt.fmtDate(record.createdAt),
    dayLabel: fmt.fmtDateCn(record.createdAt),
    relative: fmt.relativeDay(record.createdAt),
    courtName: record.courtName,
    sessionKind: record.sessionKind,
    coachName: record.coachName,
    report: report
  }
}

function buildPerformance(records, options) {
  const opts = options || {}
  const done = sortByTime(
    records.filter(function (item) {
      return item.status === 'done' && item.summary
    })
  )
  const items = done.map(toSummaryItem)
  const latest = items[items.length - 1] || null
  const previous = items[items.length - 2] || null
  const first = items[0] || null

  const trend = items.map(function (item) {
    return { label: item.dayLabel, value: item.report.summary.overall_score, date: item.dateLabel }
  })

  // 六维平均
  const dimensionNames = latest ? latest.report.dimensions.map(function (d) { return d.name }) : []
  const dimensionAvg = dimensionNames.map(function (name) {
    let sum = 0
    let count = 0
    items.forEach(function (item) {
      const found = item.report.dimensions.filter(function (d) { return d.name === name })[0]
      if (found) {
        sum += found.score
        count += 1
      }
    })
    const avg = count ? Math.round(sum / count) : 0
    const bench = latest.report.dimensions.filter(function (d) { return d.name === name })[0].benchmark
    const last = latest.report.dimensions.filter(function (d) { return d.name === name })[0].score
    return { name: name, score: last, average: avg, benchmark: bench, delta: last - avg }
  })

  // 关键指标趋势
  const metricTrends = [
    {
      key: 'score',
      label: '综合评分',
      unit: '分',
      series: items.map(function (item) {
        return { label: item.dayLabel, value: item.report.summary.overall_score }
      })
    },
    {
      key: 'distance',
      label: '累计跑动',
      unit: 'm',
      series: items.map(function (item) {
        return { label: item.dayLabel, value: Math.round(item.report.players.user.distance_m) }
      })
    },
    {
      key: 'passive',
      label: '被动击球占比',
      unit: '%',
      lowerIsBetter: true,
      series: items.map(function (item) {
        const user = item.report.players.user
        const total = Math.max(1, user.hit_count)
        return { label: item.dayLabel, value: Math.round((user.passive_hit_count / total) * 100) }
      })
    },
    {
      key: 'ballSpeed',
      label: '最高球速',
      unit: 'km/h',
      series: items.map(function (item) {
        return { label: item.dayLabel, value: Math.round(item.report.summary.ball_speed.max_kmh) }
      })
    }
  ]

  const scores = trend.map(function (point) { return point.value })
  const best = scores.length ? Math.max.apply(null, scores) : 0
  const avgScore = scores.length
    ? Math.round(scores.reduce(function (a, b) { return a + b; }, 0) / scores.length)
    : 0

  let totalDuration = 0
  let totalDistance = 0
  let totalHits = 0
  items.forEach(function (item) {
    totalDuration += engine.num(item.report.summary.metrics[0].value ? 0 : 0)
    const raw = item.report.summary
    totalDistance += item.report.players.user.distance_m
    totalHits += item.report.players.user.hit_count
    totalDuration += raw.rally_duration_s
  })

  const delta = latest && first ? latest.report.summary.overall_score - first.report.summary.overall_score : 0
  const recentDelta = latest && previous ? latest.report.summary.overall_score - previous.report.summary.overall_score : 0

  return {
    items: items.slice().reverse(),
    count: items.length,
    latest: latest,
    previous: previous,
    first: first,
    trend: trend,
    dimensionAvg: dimensionAvg,
    metricTrends: metricTrends,
    scoreStats: { best: best, average: avgScore, delta: delta, recentDelta: recentDelta },
    totals: {
      durationS: Math.round(totalDuration),
      distanceM: Math.round(totalDistance),
      hits: Math.round(totalHits),
      sessions: items.length
    },
    // 场馆同级别球员百分位（真实场景由后端按场馆/等级分桶计算）
    percentile: Math.max(18, Math.min(96, Math.round((avgScore - 42) * 1.5))),
    milestones: buildMilestones(items)
  }
}

function buildMilestones(items) {
  if (!items.length) return []
  const out = []
  const latest = items[items.length - 1]
  const scores = items.map(function (i) { return i.report.summary.overall_score })
  const best = Math.max.apply(null, scores)
  const bestItem = items[scores.indexOf(best)]
  out.push({ label: '历史最高综合评分', value: best + ' 分', date: bestItem.dateLabel })

  let bestSpeed = 0
  let speedDate = ''
  items.forEach(function (item) {
    const kmh = item.report.summary.ball_speed.max_kmh
    if (kmh > bestSpeed) {
      bestSpeed = kmh
      speedDate = item.dateLabel
    }
  })
  out.push({ label: '最快击球球速', value: Math.round(bestSpeed) + ' km/h', date: speedDate })

  const level = latest.report.summary.level_label
  const reached = items.filter(function (item) {
    return item.report.summary.level_label === level
  })
  out.push({
    label: '当前等级',
    value: level,
    date: '自 ' + reached[0].dateLabel + ' 起保持'
  })

  out.push({
    label: '累计有效击球',
    value: items.reduce(function (a, i) { return a + i.report.players.user.hit_count }, 0) + ' 次',
    date: items.length + ' 次训练累计'
  })
  return out
}

module.exports = {
  buildPerformance: buildPerformance,
  toSummaryItem: toSummaryItem,
  sortByTime: sortByTime
}
