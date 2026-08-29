export async function getState() {
  const res = await fetch('/api/state')
  return res.json()
}

export async function postJSON(path, body = {}) {
  const res = await fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return res.json()
}

export async function searchCities(query, limit = 10) {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  const res = await fetch(`/api/search?${params}`)
  return res.json()
}

export async function getCities() {
  const res = await fetch('/api/cities')
  return res.json()
}

export const api = {
  getState,
  addMember: (data) => postJSON('/members', data),
  removeMember: (name) => postJSON('/members/remove', { name }),
  round: () => postJSON('/round'),
  settleDate: () => postJSON('/settle_date'),
  discover: (minDays) => postJSON('/discover', minDays != null ? { min_stopover_days: minDays } : {}),
  synthesize: () => postJSON('/synthesize'),
  decide: (option) => postJSON('/decide', { option }),
  confirm: () => postJSON('/confirm'),
  constraint: (data) => postJSON('/constraint', data),
  reset: () => postJSON('/reset'),
  cancelBooking: () => postJSON('/cancel_booking'),
  runAutonomous: (minDays) => postJSON('/run_autonomous', minDays != null ? { min_stopover_days: minDays } : {}),
  feed: (dates = 3) => postJSON('/feed', { dates: typeof dates === 'number' ? dates : 3 }),
  selectCard: (index) => postJSON('/select_card', { index }),
  swapStopover: (cityId, cityName, minDays) => postJSON('/swap_stopover', {
    city_id: cityId, city_name: cityName, min_stopover_days: minDays
  }),
  swapDestination: (cityId, cityName, minDays) => postJSON('/swap_destination', {
    city_id: cityId, city_name: cityName, min_stopover_days: minDays
  }),
  testDates: async () => {
    const res = await fetch('/api/test_dates')
    return res.json()
  },
  searchCities,
  getCities,
}
