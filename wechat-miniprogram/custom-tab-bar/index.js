Component({
  data: {
    selected: 0,
    list: [
      { pagePath: '/pages/home/home', text: '首页', icon: 'home' },
      { pagePath: '/pages/records/records', text: '训练', icon: 'records' },
      { pagePath: '/pages/booking/booking', text: '预约', icon: 'booking' },
      { pagePath: '/pages/profile/profile', text: '我的', icon: 'profile' }
    ]
  },

  methods: {
    onTap(e) {
      const index = Number(e.currentTarget.dataset.index)
      const item = this.data.list[index]
      if (!item || index === this.data.selected) return
      wx.switchTab({ url: item.pagePath })
    }
  }
})
