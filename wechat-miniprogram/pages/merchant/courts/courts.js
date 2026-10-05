const api = require('../../../utils/api')
const fmt = require('../../../utils/format')

const TYPES = ['标准双打', '训练场', '单打场', '比赛场']

Page({
  data: {
    venue: {},
    courts: [],
    showPrice: false,
    showAdd: false,
    editing: null,
    priceInput: '',
    newCourt: { name: '', typeIndex: 0, price: 55, camera: true },
    typeNames: TYPES,
    totalOpen: 0
  },

  onShow() {
    this.load()
  },

  load() {
    const self = this
    return Promise.all([api.getVenue(), api.getCourts()]).then(function (res) {
      const courts = res[1].map(function (c) {
        return Object.assign({}, c, {
          statusText: c.maintenance ? '维护中' : c.open ? '开放中' : '已停用',
          statusClass: c.maintenance ? 'danger' : c.open ? 'ok' : ''
        })
      })
      self.setData({
        venue: res[0],
        courts: courts,
        totalOpen: courts.filter(function (c) {
          return c.open && !c.maintenance
        }).length
      })
    })
  },

  toggleOpen(e) {
    const id = e.currentTarget.dataset.id
    const value = e.detail.value
    const self = this
    api.updateCourt(id, { open: value }).then(function () {
      wx.showToast({ title: value ? '已开放预约' : '已停止预约', icon: 'none' })
      self.load()
    })
  },

  toggleMaintenance(e) {
    const id = e.currentTarget.dataset.id
    const value = e.detail.value
    const self = this
    api.updateCourt(id, { maintenance: value }).then(function () {
      wx.showToast({ title: value ? '已标记维护中' : '维护结束', icon: 'none' })
      self.load()
    })
  },

  editPrice(e) {
    const id = e.currentTarget.dataset.id
    const court = this.data.courts.filter(function (c) {
      return c.id === id
    })[0]
    if (!court) return
    this.setData({ showPrice: true, editing: court, priceInput: String(court.price) })
  },

  onPriceInput(e) {
    this.setData({ priceInput: e.detail.value })
  },

  closeSheet() {
    this.setData({ showPrice: false, showAdd: false })
  },

  savePrice() {
    const price = Math.max(0, Math.min(500, Number(this.data.priceInput) || 0))
    const self = this
    api.updateCourt(this.data.editing.id, { price: price }).then(function () {
      wx.showToast({ title: '价格已更新', icon: 'none' })
      self.setData({ showPrice: false })
      self.load()
    })
  },

  openAdd() {
    this.setData({
      showAdd: true,
      newCourt: { name: '场地 ' + (this.data.courts.length + 1), typeIndex: 0, price: 55, camera: true }
    })
  },

  onNewName(e) {
    this.setData({ 'newCourt.name': e.detail.value })
  },

  onNewType(e) {
    this.setData({ 'newCourt.typeIndex': Number(e.detail.value) })
  },

  onNewPrice(e) {
    this.setData({ 'newCourt.price': e.detail.value })
  },

  onNewCamera(e) {
    this.setData({ 'newCourt.camera': e.detail.value })
  },

  saveAdd() {
    const self = this
    const data = this.data.newCourt
    if (!String(data.name).trim()) {
      wx.showToast({ title: '请输入场地名称', icon: 'none' })
      return
    }
    api
      .addCourt({
        name: data.name,
        type: TYPES[data.typeIndex],
        price: Math.max(0, Math.min(500, Number(data.price) || 0)),
        camera: data.camera
      })
      .then(function () {
        wx.showToast({ title: '场地已新增', icon: 'none' })
        self.setData({ showAdd: false })
        self.load()
      })
  },

  goWorkbench() {
    wx.redirectTo({ url: '/pages/merchant/workbench/workbench' })
  },

  goManage() {
    wx.redirectTo({ url: '/pages/merchant/manage/manage' })
  }
})
