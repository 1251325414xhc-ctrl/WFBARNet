// utils/engine/report-engine.js
// ---------------------------------------------------------------------------
// 本文件是桌面端 src/utils/user_report.py 中 build_user_report_data_from_rally_record
// 及其全部私有算子的 1:1 JavaScript 移植（维度评分权重、等级划分、改进项判定阈值、
// 训练计划生成、球场热区坐标全部保持一致）。
//
// 因此：桌面端 WFBARNet 导出的 export_record() 原始 JSON，直接丢给
// buildReportData() 就能得到与桌面端 HTML 报告完全一致的数值结论。
// ---------------------------------------------------------------------------

const fmt = require('../format')

const PLAYER_KEYS = ['top', 'bottom']
const PLAYER_LABELS = { top: '上方球员', bottom: '下方球员' }
const ZONE_LABELS = { front: '前场', mid: '中场', back: '后场' }
const ZONE_ORDER = ['front', 'mid', 'back']
const RELIABILITY_LABELS = {
  ball_visible_rate: '球点可见率',
  pose_valid_rate: '姿态有效率',
  court_valid_rate: '球场有效率',
  avg_ball_confidence: '平均球置信度'
}
const RELIABILITY_KEYS = ['ball_visible_rate', 'pose_valid_rate', 'court_valid_rate', 'avg_ball_confidence']

const RELIABILITY_NOTES = {
  ball_visible_rate: '高速小目标跟踪质量，低于 80% 时需谨慎解读落点。',
  pose_valid_rate: '人体关键点可用比例，影响移动距离和步法判断。',
  court_valid_rate: '球场线/单应性可用比例，影响标准场地坐标。',
  avg_ball_confidence: '轨迹模型平均置信度，影响击球事件稳定性。'
}

const DIMENSION_NOTES = {
  步法覆盖: '由平均速度、最大速度、高强度移动和被动击球比例综合估计。',
  后场进攻: '后场击球占比不低，但连续压迫后的回位质量仍有提升空间。',
  网前控制: '根据前场击球次数与前场参与率估计，反映搓放、挑球和扑球机会。',
  攻防转换: '急停、连续移动距离和被动击球比例会直接影响下一拍衔接。',
  击球多样性: '由动作类别数量和前中后场覆盖度估计，类别越均衡越利于战术变化。',
  稳定性: '融合球点可见率、姿态有效率、球场有效率和击球置信度。'
}

const DIMENSION_BENCHMARK = {
  步法覆盖: 84,
  后场进攻: 82,
  网前控制: 80,
  攻防转换: 83,
  击球多样性: 81,
  稳定性: 86
}

const DIMENSION_WEIGHT = {
  步法覆盖: 1.1,
  后场进攻: 1.0,
  网前控制: 0.95,
  攻防转换: 1.0,
  击球多样性: 0.85,
  稳定性: 1.05
}

// ----------------------------- 基础工具 -----------------------------
function num(value) {
  const n = Number(value)
  if (!isFinite(n)) return 0
  return n
}

function clamp(value, lower, upper) {
  return Math.max(lower, Math.min(num(value), upper))
}

function mapping(value) {
  return value && typeof value === 'object' ? value : {}
}

function sequence(value) {
  return Array.isArray(value) ? value : []
}

function percentOf(value, maxValue) {
  if (num(maxValue) <= 0) return 0
  return clamp((num(value) / num(maxValue)) * 100, 0, 100)
}

function counterItems(value) {
  const payload = mapping(value)
  const items = []
  Object.keys(payload).forEach(function (key) {
    const count = Math.round(num(payload[key]))
    if (count > 0) items.push({ name: String(key), value: count })
  })
  items.sort(function (a, b) {
    if (b.value !== a.value) return b.value - a.value
    return a.name < b.name ? -1 : 1
  })
  return items
}

function zoneCounts(value) {
  const payload = mapping(value)
  const out = {}
  ZONE_ORDER.forEach(function (key) {
    out[key] = Math.max(0, Math.round(num(payload[key])))
  })
  return out
}

function zoneCoverageCount(zones) {
  let total = 0
  ZONE_ORDER.forEach(function (key) {
    if (num(zones[key]) > 0) total += 1
  })
  return total
}

function strokeRatio(strokes, keyword) {
  let total = 0
  let matched = 0
  strokes.forEach(function (item) {
    total += num(item.value)
    if (String(item.name).indexOf(keyword) >= 0) matched += num(item.value)
  })
  if (total <= 0) return 0
  return matched / total
}

function levelLabel(score) {
  const s = num(score)
  if (s >= 88) return '校队竞技级'
  if (s >= 78) return '业余高阶'
  if (s >= 68) return '业余进阶'
  if (s >= 55) return '稳定入门'
  return '基础建立期'
}

function levelNote(score) {
  const s = num(score)
  if (s >= 88) return '具备较强比赛对抗能力，重点转向战术细节。'
  if (s >= 78) return '技术框架较完整，短板主要在高压衔接。'
  if (s >= 68) return '多拍能力已经成型，建议强化步法与落点变化。'
  if (s >= 55) return '能完成基础回合，优先建立稳定动作模式。'
  return '先从握拍、发接发和基础移动开始。'
}

// ----------------------------- 六维评分 -----------------------------
function dimensionScores(summary, user, reliability, strokes, zones) {
  const hitTotal = Math.max(
    1,
    Math.round(num(user.hit_count) || num(summary.rally_hit_count) || (zones.front + zones.mid + zones.back))
  )
  const passiveRatio = num(user.passive_hit_count) / hitTotal
  const zoneTotal = Math.max(1, zones.front + zones.mid + zones.back)
  const frontRatio = zones.front / zoneTotal
  const backRatio = zones.back / zoneTotal
  const smashRatio = strokeRatio(strokes, '杀球')
  let strokeCount = 0
  strokes.forEach(function (item) {
    if (num(item.value) > 0) strokeCount += 1
  })
  let reliabilitySum = 0
  RELIABILITY_KEYS.forEach(function (key) {
    reliabilitySum += num(reliability[key])
  })
  const reliabilityAvg = reliabilitySum / 4

  const footwork =
    56 +
    Math.min(13, num(user.avg_speed_mps) * 8.0) +
    Math.min(13, num(user.max_speed_mps) * 2.2) +
    Math.min(8, num(user.high_intensity_count) * 0.35) -
    Math.min(9, passiveRatio * 18)
  const backAttack = 58 + backRatio * 18 + smashRatio * 28 - passiveRatio * 10
  const frontControl = 54 + frontRatio * 46 + Math.min(8, zones.front * 0.8)
  const transition =
    64 +
    Math.min(8, num(user.max_continuous_m) * 0.9) -
    Math.min(12, num(user.stop_count) * 0.23) -
    passiveRatio * 18
  const variety = 50 + Math.min(28, strokeCount * 4.8) + Math.min(12, zoneCoverageCount(zones) * 4)
  const stability =
    42 +
    reliabilityAvg * 42 +
    num(summary.hit_confidence_avg) * 16 -
    Math.min(8, num(summary.out_of_frame_count) * 2)

  const raw = [
    { name: '步法覆盖', value: footwork },
    { name: '后场进攻', value: backAttack },
    { name: '网前控制', value: frontControl },
    { name: '攻防转换', value: transition },
    { name: '击球多样性', value: variety },
    { name: '稳定性', value: stability }
  ]

  return raw.map(function (item) {
    return {
      name: item.name,
      score: Math.round(clamp(item.value, 0, 100)),
      benchmark: DIMENSION_BENCHMARK[item.name],
      weight: DIMENSION_WEIGHT[item.name],
      note: DIMENSION_NOTES[item.name]
    }
  })
}

function overallScoreOf(dimensions) {
  let weighted = 0
  let weightSum = 0
  dimensions.forEach(function (item) {
    weighted += num(item.score) * num(item.weight)
    weightSum += num(item.weight)
  })
  if (weightSum <= 0) return 0
  return Math.round(weighted / weightSum)
}

// ----------------------------- 改进建议 -----------------------------
function buildImprovements(dimensions, summary, user, reliability, zones, strokes) {
  const scores = {}
  dimensions.forEach(function (item) {
    scores[item.name] = num(item.score)
  })
  const hitTotal = Math.max(
    1,
    Math.round(num(user.hit_count) || num(summary.rally_hit_count) || (zones.front + zones.mid + zones.back))
  )
  const passiveHits = Math.round(num(user.passive_hit_count))
  const zoneTotal = Math.max(1, zones.front + zones.mid + zones.back)
  const frontRatio = zones.front / zoneTotal
  const backRatio = zones.back / zoneTotal
  let smashCount = 0
  strokes.forEach(function (item) {
    if (String(item.name).indexOf('杀') >= 0) smashCount = Math.round(num(item.value))
  })

  const improvements = []

  if (num(scores['攻防转换']) < 76 || passiveHits / hitTotal > 0.24) {
    improvements.push({
      priority: '优先级 1',
      title: '后场回位后的第一步偏慢',
      evidence:
        '目标用户被动击球 ' + passiveHits + ' 次，占有效击球约 ' +
        ((passiveHits / hitTotal) * 100).toFixed(1) + '%；急停 ' +
        Math.round(num(user.stop_count)) + ' 次，说明连续压迫后衔接会变紧。',
      suggestion: '练习后场击球后向中线小跳回位，下一拍先找重心再启动，避免杀球或高远球后直接站死。',
      tags: ['回位', '攻防转换', '第一步']
    })
  }

  if (frontRatio < 0.24 || num(scores['网前控制']) < 72) {
    improvements.push({
      priority: '优先级 2',
      title: '网前参与率偏低',
      evidence:
        '前场击球 ' + zones.front + ' 次，占比 ' + (frontRatio * 100).toFixed(1) +
        '%；多数击球集中在中后场，主动上网终结机会不足。',
      suggestion: '加入搓放后跟进、挑后封网和半场扑球练习，把对手回球从后场拉到网前处理。',
      tags: ['网前', '搓放', '封网']
    })
  }

  if (backRatio > 0.38 && smashCount >= 6) {
    improvements.push({
      priority: '优先级 3',
      title: '后场进攻质量需要配合落点变化',
      evidence:
        '后场击球 ' + zones.back + ' 次，杀球 ' + smashCount +
        ' 次，说明进攻意图明确，但落点变化不足时容易被对手借力防起。',
      suggestion: '后场不要只追求重杀，可用杀吊结合、点杀追身和高远压底线交替，减少同一区域连续两拍。',
      tags: ['后场', '落点', '杀吊结合']
    })
  }

  let reliabilitySum = 0
  RELIABILITY_KEYS.forEach(function (key) {
    reliabilitySum += num(reliability[key])
  })
  if (reliabilitySum / 4 < 0.82) {
    improvements.push({
      priority: '数据提示',
      title: '拍摄角度仍可优化',
      evidence: '球场有效率或姿态有效率未达到高可信区间，部分高速球与边线附近动作可能低估。',
      suggestion: '建议使用固定机位，尽量拍完整双打边线和底线，保证球场白线清晰，帧率保持 50fps 或以上。',
      tags: ['数据可信度', '机位', '帧率']
    })
  }

  const fallback = [
    {
      priority: '巩固项',
      title: '提升击球前准备节奏',
      evidence: '平均击球间隔和启动次数显示，本次回合节奏已经较紧，下一阶段应减少多余小碎步。',
      suggestion: '每拍结束后用一次分腿垫步统一启动时机，训练中以“击球后回位、对手击球时分腿”为口令。',
      tags: ['节奏', '分腿垫步', '准备动作']
    },
    {
      priority: '巩固项',
      title: '保持动作类型多样性',
      evidence: '本次识别到多种击球类型，具备基本变化能力。',
      suggestion: '继续保持高远、吊、杀、搓的组合，不要在连续得分或丢分后只依赖单一线路。',
      tags: ['战术', '多拍', '变化']
    }
  ]
  for (let i = 0; i < fallback.length; i += 1) {
    if (improvements.length >= 3) break
    improvements.push(fallback[i])
  }
  return improvements.slice(0, 4)
}

function trainingPlan(improvements) {
  const titles = improvements.map(function (item) {
    return String(item.title || '')
  })
  const hasBack = titles.some(function (t) {
    return t.indexOf('后场') >= 0 || t.indexOf('回位') >= 0
  })
  const hasFront = titles.some(function (t) {
    return t.indexOf('网前') >= 0
  })
  const plan = []
  if (hasBack) {
    plan.push({
      title: '后场两点回位',
      content: '后场正手、头顶各 12 球为一组，击球后必须回到中线后再启动下一拍，重点看第一步是否干净。',
      frequency: '每次 4 组',
      target: '被动击球占比下降 5%'
    })
  }
  if (hasFront) {
    plan.push({
      title: '搓放跟进与封网',
      content: '半场网前搓放 10 球后接 6 球扑推，训练从低重心过渡到前压封网的节奏。',
      frequency: '每周 2 次',
      target: '前场击球占比提升到 25%'
    })
  }
  plan.push({
    title: '杀吊结合线路',
    content: '同一后场点位按“高远、吊、点杀、重杀”循环，要求落点覆盖直线、斜线和追身。',
    frequency: '每次 15 分钟',
    target: '后场进攻得分质量提升'
  })
  plan.push({
    title: '固定机位复测',
    content: '两周后用同一机位重新上传 3 段 60 秒训练视频，对比综合评分、被动击球和网前参与率。',
    frequency: '两周后复测',
    target: '报告指标可横向比较'
  })
  return plan.slice(0, 4)
}

function takeaways(overallScore, dimensions, user, reliability) {
  let weakest = null
  let strongest = null
  dimensions.forEach(function (item) {
    if (!weakest || num(item.score) < num(weakest.score)) weakest = item
    if (!strongest || num(item.score) > num(strongest.score)) strongest = item
  })
  weakest = weakest || { name: '攻防转换', score: 0 }
  strongest = strongest || { name: '稳定性', score: 0 }
  let reliabilitySum = 0
  RELIABILITY_KEYS.forEach(function (key) {
    reliabilitySum += num(reliability[key])
  })
  const reliabilityAvg = reliabilitySum / 4
  return [
    '综合评分 ' + overallScore + '/100，当前更接近' + levelLabel(overallScore) +
      '：已有稳定多拍能力，但还未完全进入比赛型高压节奏。',
    '优势维度是' + strongest.name + '（' + num(strongest.score).toFixed(0) + '分），说明本次视频中的基础输出较稳定。',
    '短板维度是' + weakest.name + '（' + num(weakest.score).toFixed(0) + '分），建议优先安排专项训练。',
    '目标用户累计跑动 ' + num(user.distance_m).toFixed(1) + 'm，最大速度 ' +
      num(user.max_speed_mps).toFixed(2) + 'm/s，具备中等偏上的移动能力。',
    '本次数据平均可信度约 ' + (reliabilityAvg * 100).toFixed(1) + '%，适合做训练复盘和趋势比较。'
  ]
}

function playerReport(player, fallbackLabel) {
  const p = mapping(player)
  return {
    label: String(p.label || fallbackLabel),
    distance_m: num(p.distance_m),
    avg_speed_mps: num(p.avg_speed_mps),
    max_speed_mps: num(p.max_speed_mps),
    start_count: num(p.start_count),
    stop_count: num(p.stop_count),
    passive_hit_count: num(p.passive_hit_count),
    hit_count: num(p.hit_count)
  }
}

function courtHeatPoints(zones, userKey) {
  const baseY =
    userKey === 'bottom'
      ? { front: 760.0, mid: 965.0, back: 1215.0 }
      : { front: 580.0, mid: 375.0, back: 145.0 }
  const offsets = { front: -78.0, mid: 54.0, back: 118.0 }
  let total = 0
  ZONE_ORDER.forEach(function (key) {
    total += num(zones[key])
  })
  total = Math.max(1, total)
  const points = []
  ZONE_ORDER.forEach(function (key) {
    const value = num(zones[key])
    if (value <= 0) return
    points.push({
      x: 305.0 + offsets[key],
      y: baseY[key],
      radius: 34.0 + (value / total) * 70.0,
      intensity: 0.28 + (value / total) * 0.75,
      label: ZONE_LABELS[key] + '热区 ' + value + ' 次'
    })
  })
  if (!points.length) {
    points.push({ x: 305.0, y: 1000.0, radius: 42.0, intensity: 0.5, label: '默认站位热区' })
  }
  return points
}

function reliabilityItems(reliability) {
  return RELIABILITY_KEYS.map(function (key) {
    return {
      key: key,
      label: RELIABILITY_LABELS[key],
      value: clamp(num(reliability[key]), 0, 1),
      note: RELIABILITY_NOTES[key]
    }
  })
}

function hitEvents(rawHits, userKey) {
  if (!Array.isArray(rawHits)) return []
  return rawHits.slice(0, 30).map(function (hit) {
    const payload = mapping(hit)
    const player = String(payload.player || '')
    return {
      time: fmt.fmtTimeMs(payload.timestamp_ms),
      player: player,
      player_label: String(
        payload.player_label || (player === userKey ? '目标用户' : PLAYER_LABELS[player] || player)
      ),
      zone_label: ZONE_LABELS[payload.zone] || payload.zone || '未知',
      stroke: String(payload.stroke || '轨迹击球'),
      confidence: num(payload.confidence || payload.event_confidence),
      passive: !!payload.passive
    }
  })
}

function ballSpeedOf(summary) {
  const speed = mapping(summary.ball_speed)
  return {
    avg_mps: num(speed.avg_mps),
    max_mps: num(speed.max_mps),
    avg_kmh: num(speed.avg_kmh) || num(speed.avg_mps) * 3.6,
    max_kmh: num(speed.max_kmh) || num(speed.max_mps) * 3.6
  }
}

// ----------------------------- 主入口 -----------------------------
/**
 * 把 WFBARNet 的原始回合记录（export_record 结果）转换为报告视图数据。
 * 与桌面端 build_user_report_data_from_rally_record 字段完全对应。
 */
function buildReportData(record, options) {
  const opts = options || {}
  const summary = mapping(record && record.summary)
  const details = mapping(record && record.details)
  const players = mapping(summary.players)
  const userPlayer = opts.userPlayer || 'bottom'
  const userKey = PLAYER_KEYS.indexOf(userPlayer) >= 0 ? userPlayer : 'bottom'
  const opponentKey = userKey === 'bottom' ? 'top' : 'bottom'
  const user = mapping(players[userKey])
  const opponent = mapping(players[opponentKey])
  const reliability = mapping(summary.data_reliability)
  const strokes = counterItems(summary.stroke_distribution)
  const zones = zoneCounts(user.zone_hits)
  const dimensions = dimensionScores(summary, user, reliability, strokes, zones)
  const overallScore = overallScoreOf(dimensions)
  const improvements = buildImprovements(dimensions, summary, user, reliability, zones, strokes)
  const speed = ballSpeedOf(summary)

  const videoName = String(
    opts.videoName ||
      (record && record.video_name) ||
      (record && record.rally_name) ||
      summary.rally_name ||
      'uploaded_video.mp4'
  )

  return {
    meta: {
      title: '羽毛球水平分析报告',
      subtitle: '上传视频后由 WFBARNet 自动生成的训练复盘',
      athlete_name: opts.athleteName || '训练用户',
      video_name: videoName,
      generated_at: opts.generatedAt || summary.generated_at || '',
      pipeline: 'TrackNetV3 轨迹 + YOLO Pose 姿态 + 球场单应性 + BST 击球识别',
      user_player: PLAYER_LABELS[userKey],
      user_player_key: userKey,
      rally_state: String(summary.rally_state || '回合结束')
    },
    summary: {
      overall_score: overallScore,
      level_label: levelLabel(overallScore),
      level_note: levelNote(overallScore),
      takeaways: takeaways(overallScore, dimensions, user, reliability),
      metrics: [
        { label: '分析时长', value: fmt.fmtSeconds(summary.duration_s), hint: '上传视频有效片段' },
        { label: '有效击球', value: Math.round(num(summary.rally_hit_count)) + ' 次', hint: '轨迹/BST 合并后' },
        { label: '目标用户跑动', value: num(user.distance_m).toFixed(1) + ' m', hint: '标准球场坐标累计' },
        {
          label: '平均击球间隔',
          value: fmt.fmtSeconds(num(summary.avg_hit_interval_ms) / 1000.0),
          hint: '节奏越低越紧凑'
        }
      ],
      ball_speed: speed,
      motion_intensity_score: num(summary.motion_intensity_score),
      high_intensity_count: Math.round(num(summary.high_intensity_count)),
      landing_count: Math.round(num(summary.landing_count)),
      out_of_frame_count: Math.round(num(summary.out_of_frame_count)),
      hit_confidence_avg: num(summary.hit_confidence_avg),
      rally_duration_s: num(summary.rally_duration_s),
      frame_count: Math.round(num(summary.frame_count)),
      avg_hit_interval_ms: num(summary.avg_hit_interval_ms)
    },
    dimensions: dimensions,
    players: {
      user: playerReport(user, '目标用户'),
      opponent: playerReport(opponent, '对手/陪练')
    },
    stroke_distribution: strokes,
    zone_distribution: ZONE_ORDER.map(function (key) {
      return { key: key, label: ZONE_LABELS[key], value: zones[key] }
    }),
    court_heat: courtHeatPoints(zones, userKey),
    reliability: reliabilityItems(reliability),
    improvements: improvements,
    training_plan: trainingPlan(improvements),
    events: hitEvents(details.hits, userKey),
    disclaimer:
      '本报告基于视频中的轨迹、姿态和球场投影自动估计，适合训练复盘，不作为比赛裁判或等级认证依据。'
  }
}

/** 轻量摘要：列表页只需要评分与几个关键数 */
function summarize(record, options) {
  const report = buildReportData(record, options)
  const summary = report.summary
  return {
    overall_score: summary.overall_score,
    level_label: summary.level_label,
    level_note: summary.level_note,
    hit_count: Math.round(num(mapping(record.summary).rally_hit_count)),
    duration_s: num(mapping(record.summary).duration_s),
    distance_m: report.players.user.distance_m,
    max_ball_kmh: summary.ball_speed.max_kmh,
    passive_hit_count: report.players.user.passive_hit_count,
    weakest: weakestDimension(report.dimensions),
    strongest: strongestDimension(report.dimensions)
  }
}

function weakestDimension(dimensions) {
  let target = dimensions[0] || { name: '', score: 0 }
  dimensions.forEach(function (item) {
    if (num(item.score) < num(target.score)) target = item
  })
  return { name: target.name, score: num(target.score) }
}

function strongestDimension(dimensions) {
  let target = dimensions[0] || { name: '', score: 0 }
  dimensions.forEach(function (item) {
    if (num(item.score) > num(target.score)) target = item
  })
  return { name: target.name, score: num(target.score) }
}

module.exports = {
  PLAYER_LABELS: PLAYER_LABELS,
  ZONE_LABELS: ZONE_LABELS,
  ZONE_ORDER: ZONE_ORDER,
  buildReportData: buildReportData,
  summarize: summarize,
  dimensionScores: dimensionScores,
  overallScoreOf: overallScoreOf,
  levelLabel: levelLabel,
  levelNote: levelNote,
  weakestDimension: weakestDimension,
  strongestDimension: strongestDimension,
  num: num,
  clamp: clamp
}
