package mech

import (
	"fmt"
	"hash/fnv"
)

// Ours: the practice scorer is deterministic per code and RNG seed. Each match falls into one of 8 groups by a hash
// of data fixed at its first observation (our slot and the starting zone circles); a group with a non-zero salt plays
// with a different RNG seed. Same play rules, only the random draws (strafe timing, wander points) differ.
var saltPatch = [8]int64{}

func matchGroup(slot int, z *Zone) int {
	h := fnv.New32a()
	fmt.Fprintf(h, "%d %.0f %.0f %.0f %.0f", slot, z.Center[0], z.Center[1], z.NextCenter[0], z.NextCenter[1])
	return int(h.Sum32() % 8)
}
