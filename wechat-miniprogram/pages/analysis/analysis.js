const api = require('../../utils/api')

const STAGES = [
  { key: 'upload', label: '视频解码与切片', hint: '抽取关键帧，估计机位与帧率' },
  { key: 'court', label: '自动球场标定', hint: '白线检测 + 单应性验证' },
  { key: 'track', label: '轨迹识别 TrackNetV3', hint: '羽毛球小目标跨帧追踪' },
  { key: 'pose', label: '姿态估计 YOLO Pose', hint: '人体关键点与站位投影' },
  { key: 'bst', label: '击球识别 BST', hint: '动作类型分类与击球事件合并' },
  { key: 'report', label: '生成分析报告', hint: '六维评分、热区与训练建议' }
]

const DEMO_VIDEO = {
  name: 'demo_training_clip.mp4',
  sizeText: '28.6 MB',
  durationText: '3 分 12 秒'
}

Page({
  data: {
    courts: [],
    courtNames: [],
    courtIndex: 0,
    file: null,
    stages: STAGES,
    running: false,
    progress: 0,
    stageIndex: 0,
    done: false,
    withCoach: true,
    withSpin: true,
    withHeat: true,
    log: []
  },

  onLoad() {
    const self = this
    api.getCourts().then(function (courts) {
      const open = courts.filter(function (c) {
        return c.open && !c.maintenance
      })
      self.setData({
        courts: open,
        courtNames: open.map(function (c) {
          return c.name + ' · ' + c.type
        })
      })
    })
  },

  onUnload() {
    this.stopTimer()
  },

  stopTimer() {
    if (this._timer) {
      clearInterval(this._timer)
      this._timer = null
    }
  },

  chooseVideo() {
    const self = this
    if (!wx.chooseMedia) {
      wx.showToast({ title: '当前环境不支持选视频', icon: 'none' })
      return
    }
    wx.chooseMedia({
      count: 1,
      mediaType: ['video'],
      sourceType: ['album', 'camera'],
      maxDuration: 60,
      camera: 'back',
      success(res) {
        const file = res.tempFiles[0]
        const sizeMb = (file.size / 1024 / 1024).toFixed(1)
        const duration = file.duration ? Math.round(file.duration) : 0
        self.setData({
          file: {
            path: file.tempFilePath,
            name: 'upload_' + Date.now() + '.mp4',
            sizeText: sizeMb + ' MB',
            durationText: duration ? Math.floor(duration / 60) + ' 分 ' + (duration % 60) + ' 秒' : '未知'
          }
        })
      },
      fail() {}
    })
  },

  useDemo() {
    this.setData({ file: Object.assign({ path: '' }, DEMO_VIDEO) })
  },

  onCourtChange(e) {
    this.setData({ courtIndex: Number(e.detail.value) })
  },

  toggleCoach(e) {
    this.setData({ withCoach: e.detail.value })
  },

  toggleSpin(e) {
    this.setData({ withSpin: e.detail.value })
  },

  toggleHeat(e) {
    this.setData({ withHeat: e.detail.value })
  },

  start() {
    const self = this
    if (!this.data.file) {
      wx.showToast({ title: '请先选择或录制一段视频', icon: 'none' })
      return
    }
    if (!this.data.courts.length) {
      wx.showToast({ title: '暂无可用场地', icon: 'none' })
      return
    }
    const court = this.data.courts[this.data.courtIndex]
    api
      .createAnalysisTask({ fileName: this.data.file.name, courtName: court.name })
      .then(function (task) {
        self._taskId = task.id
        self.setData({ running: true, progress: 0, stageIndex: 0, done: false, log: [] })
        self.appendLog('任务已提交：' + court.name + ' · ' + self.data.file.name)
        self.runTimer()
      })
  },

  appendLog(text) {
    const now = new Date()
    const stamp =
      ('0' + now.getHours()).slice(-2) + ':' + ('0' + now.getMinutes()).slice(-2) + ':' +
      ('0' + now.getSeconds()).slice(-2)
    this.setData({ log: this.data.log.concat(['[' + stamp + '] ' + text]) })
  },

  runTimer() {
    const self = this
    this.stopTimer()
    let progress = 0
    this._timer = setInterval(function () {
      progress += Math.random() * 7 + 3
      if (progress >= 100) {
        progress = 100
        self.stopTimer()
        const stageIndex = STAGES.length - 1
        self.setData({ progress: 100, stageIndex: stageIndex, running: false, done: true })
        self.appendLog('分析完成，报告已生成')
        api.updateAnalysisTask(self._taskId, { status: 'done', progress: 100, stage: '已完成' })
        return
      }
      const stageIndex = Math.min(
        STAGES.length - 1,
        Math.floor((progress / 100) * STAGES.length)
      )
      const changed = stageIndex !== self.data.stageIndex
      self.setData({ progress: Math.round(progress), stageIndex: stageIndex })
      if (changed) {
        self.appendLog('进入阶段：' + STAGES[stageIndex].label)
      }
      if (self._lastPersist !== stageIndex) {
        self._lastPersist = stageIndex
        api.updateAnalysisTask(self._taskId, {
          status: 'analyzing',
          progress: Math.round(progress),
          stage: STAGES[stageIndex].label
        })
      }
    }, 420)
  },

  goRecords() {
    wx.switchTab({ url: '/pages/records/records' })
  },

  goReport() {
    wx.navigateTo({ url: '/pages/report/report?id=rec-08' })
  },

  reset() {
    this.stopTimer()
    this.setData({ file: null, running: false, progress: 0, stageIndex: 0, done: false, log: [] })
  }
})
