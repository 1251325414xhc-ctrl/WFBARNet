// utils/engine/mock-records.js
// ---------------------------------------------------------------------------
// 模拟「场馆智能分析一体机 / 桌面端 WFBARNet」上传的原始回合记录。
// 字段结构与 src/postprocess/rally_stats.py 的 RallyStatsAccumulator.export_record()
// 完全对齐：{ rally_id, rally_name, summary, details }
//
// 接入真后端时删除本文件即可，api.js 里的 USE_MOCK = false 会走 HTTP。
// ---------------------------------------------------------------------------

const fmt = require('../format')

const STROKE_TYPES = ['高远球', '杀球', '吊球', '挑球', '搓放', '平抽挡']
const ZONES = ['front', 'mid', 'back']
const SURNAMES = ['陈', '林', '苏', '何', '顾', '沈', '周', '许']
const GIVEN = ['知遥', '一诺', '嘉禾', '亦舟', '清和', '望舒', '子谦', '书棠']

/** 确定性伪随机（同一 session 每次生成结果一致，避免每次进页面数据跳动） */
function makeRandom(seed) {
  let s = seed % 2147483647
  if (s <= 0) s += 2147483646
  return function () {
    s = (s * 16807) % 2147483647
    return (s - 1) / 2147483646
  }
}

// 训练课表：level 控制本次的技术状态，驱动所有底层指标（整体呈稳步上升）
const SESSIONS = [
  { daysAgo: 47, level: 0.20, court: '3 号场', kind: '发球机多球', coach: '陈教练' },
  { daysAgo: 41, level: 0.30, court: '2 号场', kind: '正手高远球专项', coach: '陈教练' },
  { daysAgo: 34, level: 0.42, court: '3 号场', kind: '步法多球', coach: '陈教练' },
  { daysAgo: 27, level: 0.54, court: '1 号场', kind: '半场对抗', coach: '吴教练' },
  { daysAgo: 20, level: 0.64, court: '4 号场', kind: '杀吊结合', coach: '吴教练' },
  { daysAgo: 13, level: 0.74, court: '2 号场', kind: '网前搓放封网', coach: '吴教练' },
  { daysAgo: 6, level: 0.84, court: '2 号场', kind: '实战对抗', coach: '吴教练' },
  { daysAgo: 2, level: 0.90, court: '5 号场', kind: '全场实战复盘', coach: '吴教练' }
]

// 击球类型按技术成熟度逐步解锁：先高远/挑球，进阶后加入吊球、平抽挡、搓放，最后才是杀球
const STROKE_ORDER = ['高远球', '挑球', '吊球', '平抽挡', '搓放', '杀球']

function buildStrokeDistribution(rng, total, level) {
  const activeCount = Math.max(2, Math.min(STROKE_ORDER.length, 2 + Math.round(level * 4.6)))
  const active = STROKE_ORDER.slice(0, activeCount)
  const weightMap = {
    高远球: 0.30,
    挑球: 0.17 - 0.05 * level,
    吊球: 0.15 + 0.04 * level,
    平抽挡: 0.13 + 0.05 * level,
    搓放: 0.11 + 0.07 * level,
    杀球: 0.06 + 0.20 * level
  }
  let sum = 0
  active.forEach(function (key) {
    sum += weightMap[key]
  })
  const counts = {}
  let assigned = 0
  const exactList = active.map(function (key) {
    const exact = (total * weightMap[key]) / sum
    const base = Math.max(1, Math.floor(exact))
    counts[key] = base
    assigned += base
    return { key: key, frac: exact - Math.floor(exact) }
  })
  exactList.sort(function (a, b) {
    return b.frac - a.frac
  })
  let rest = total - assigned
  let guard = 0
  let i = 0
  while (rest !== 0 && guard < 400) {
    const key = exactList[i % exactList.length].key
    if (rest > 0) {
      counts[key] += 1
      rest -= 1
    } else if (counts[key] > 1) {
      counts[key] -= 1
      rest += 1
    }
    i += 1
    guard += 1
  }
  return counts
}


function buildPlayer(level, rng, isUser, baseLabel) {
  // 陪练球员保持稳定的中上水平，用来做「与固定对手的对比」
  const l = isUser ? level : 0.66
  const front = Math.round(4 + l * 10 + rng() * 1.5)
  const mid = Math.round(8 + l * 9 + rng() * 1.5)
  const back = Math.round(9 + l * 15 + rng() * 1.5)
  const hitCount = front + mid + back
  return {
    label: baseLabel,
    distance_m: Number((55 + l * 75 + rng() * 6).toFixed(1)),
    avg_speed_mps: Number((0.86 + l * 0.72 + rng() * 0.08).toFixed(2)),
    max_speed_mps: Number((3.3 + l * 1.9 + rng() * 0.25).toFixed(2)),
    stop_count: Math.round(33 - l * 16 + rng() * 3),
    start_count: Math.round(24 + l * 14 + rng() * 3),
    hit_count: hitCount,
    zone_hits: { front: front, mid: mid, back: back },
    passive_hit_count: Math.round(Math.max(2, (0.33 - l * 0.20) * hitCount + rng() * 2)),
    high_intensity_count: Math.round(5 + l * 24 + rng() * 3),
    max_continuous_m: Number((3.9 + l * 5.8 + rng() * 0.6).toFixed(1)),
    front_back_movement_ratio: Number((0.36 + l * 0.20).toFixed(2)),
    left_right_movement_ratio: Number((0.64 - l * 0.20).toFixed(2)),
    avg_stance_depth_cm: Number((340 + l * 100 + rng() * 15).toFixed(1))
  }
}


function buildHits(rng, count, userKey, strokes) {
  const pool = []
  Object.keys(strokes).forEach(function (name) {
    for (let i = 0; i < strokes[name]; i += 1) pool.push(name)
  })
  const hits = []
  let t = 2600
  for (let i = 0; i < count && i < pool.length; i += 1) {
    const stroke = pool[Math.floor(rng() * pool.length)]
    const player = i % 2 === 0 ? userKey : userKey === 'bottom' ? 'top' : 'bottom'
    const zone = ZONES[Math.floor(rng() * ZONES.length)]
    hits.push({
      timestamp_ms: Math.round(t),
      player: player,
      player_label: player === userKey ? '目标用户' : '陪练球员',
      zone: zone,
      stroke: stroke,
      confidence: Number((0.74 + rng() * 0.18).toFixed(2)),
      court_xy: [Number((120 + rng() * 380).toFixed(1)), Number((200 + rng() * 1000).toFixed(1))],
      passive: rng() < 0.26
    })
    t += 1500 + rng() * 2200
  }
  return hits
}

function buildRecord(session, index) {
  const rng = makeRandom(20260901 + index * 7919)
  const level = session.level
  const durationS = Number((105 + level * 88 + rng() * 12).toFixed(1))
  const rallyDuration = Number((durationS * (0.62 + rng() * 0.12)).toFixed(1))
  const user = buildPlayer(level, rng, true, '目标用户')
  const opponent = buildPlayer(level, rng, false, '陪练球员')
  const strokeTotal = user.hit_count
  const strokes = buildStrokeDistribution(rng, strokeTotal, level)
  const createdAt = new Date(Date.now() - session.daysAgo * 86400000 - 3600000)

  const summary = {
    rally_name: 'training-' + fmt.dateKey(createdAt) + '.mp4',
    duration_s: durationS,
    rally_duration_s: rallyDuration,
    rally_state: '回合结束',
    frame_count: Math.round(durationS * 30),
    rally_hit_count: strokeTotal,
    landing_count: Math.round(strokeTotal * (0.16 + rng() * 0.08)),
    out_of_frame_count: Math.max(0, Math.round(4 - level * 3.4 + rng() * 1.4)),
    avg_hit_interval_ms: Math.round(2820 - level * 800 + rng() * 120),
    hit_confidence_avg: Number((0.70 + level * 0.16 + rng() * 0.02).toFixed(2)),
    motion_intensity_score: Number((48 + level * 42 + rng() * 3).toFixed(1)),
    high_intensity_count: Math.round(6 + level * 26 + rng() * 3),
    stroke_distribution: strokes,
    data_reliability: {
      ball_visible_rate: Number((0.80 + level * 0.12).toFixed(2)),
      pose_valid_rate: Number((0.76 + level * 0.13).toFixed(2)),
      court_valid_rate: Number((0.70 + level * 0.17).toFixed(2)),
      avg_ball_confidence: Number((0.72 + level * 0.11).toFixed(2))
    },
    ball_speed: {
      avg_mps: Number((24 + level * 16 + rng() * 3).toFixed(1)),
      max_mps: Number((52 + level * 38 + rng() * 6).toFixed(1)),
      current_valid: true
    },
    rally_start_ms: 3200,
    rally_end_ms: Math.round(3200 + rallyDuration * 1000),
    players: { top: opponent, bottom: user }
  }
  summary.ball_speed.avg_kmh = Number((summary.ball_speed.avg_mps * 3.6).toFixed(1))
  summary.ball_speed.max_kmh = Number((summary.ball_speed.max_mps * 3.6).toFixed(1))

  return {
    id: 'rec-' + fmt.pad2(index + 1),
    rally_id: 'wfbar-' + fmt.dateKey(createdAt),
    rally_name: summary.rally_name,
    summary: summary,
    details: { hits: buildHits(rng, Math.min(16, strokeTotal), 'bottom', strokes) },
    // --- 小程序侧附加的业务字段（后端返回时一并带上） ---
    createdAt: createdAt.toISOString(),
    courtName: session.court,
    sessionKind: session.kind,
    coachName: session.coach,
    status: 'done',
    athleteName: '林知遥',
    venueName: '羽动星空智慧球馆 · 江大店'
  }
}


/** 待分析任务：客户已上传视频，系统尚未出结果 */
function buildPendingRecords() {
  const now = Date.now()
  return [
    {
      id: 'rec-p1',
      rally_id: 'wfbar-pending-01',
      rally_name: 'IMG_20260920_1932.mov',
      status: 'pending',
      progress: 0,
      createdAt: new Date(now - 2 * 3600000).toISOString(),
      courtName: '2 号场',
      sessionKind: '客户自助上传',
      coachName: '待分配',
      athleteName: '林知遥',
      venueName: '羽动星空智慧球馆 · 江大店',
      summary: null,
      details: null
    },
    {
      id: 'rec-p2',
      rally_id: 'wfbar-pending-02',
      rally_name: 'IMG_20260918_2041.mov',
      status: 'analyzing',
      progress: 62,
      stage: '姿态估计',
      createdAt: new Date(now - 26 * 3600000).toISOString(),
      courtName: '5 号场',
      sessionKind: '教练课后录像',
      coachName: '吴教练',
      athleteName: '林知遥',
      venueName: '羽动星空智慧球馆 · 江大店',
      summary: null,
      details: null
    }
  ]
}

const RECORDS = SESSIONS.map(buildRecord)

module.exports = {
  RECORDS: RECORDS,
  PENDING: buildPendingRecords(),
  STROKE_TYPES: STROKE_TYPES,
  makeRandom: makeRandom,
  guestName: function (seed) {
    const rng = makeRandom(seed || 42)
    return SURNAMES[Math.floor(rng() * SURNAMES.length)] + GIVEN[Math.floor(rng() * (GIVEN.length - 1))]
  }
}
