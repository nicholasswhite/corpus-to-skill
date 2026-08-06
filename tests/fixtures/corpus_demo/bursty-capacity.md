# Bursty Capacity Trade-offs

This is an original synthetic CC0 fixture for offline tests.

- Teams should not use bounded queues when workloads are bursty and spare capacity is abundant.
- Bounded queues can increase rejection rates.
- Operators should queue requests when spare capacity is expected soon if request completion is the objective.
