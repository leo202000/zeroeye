"""
Tests for data_generator deterministic seed support (bounty #4).

Verifies that seeding produces reproducible output across runs, that
different seeds produce different data, and that the helper functions
(phone/email/datetime/gaussian) honour an injected rng.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import random
from data_generator import (
    DataGenerator,
    random_phone,
    random_email,
    random_datetime,
    gaussian_random,
)


class TestDeterministicSeed(unittest.TestCase):
    def test_same_seed_identical_users(self):
        g1 = DataGenerator(seed=42)
        g2 = DataGenerator(seed=42)
        u1 = g1.generate_users(50)
        u2 = g2.generate_users(50)
        self.assertEqual(u1, u2)

    def test_different_seed_different_users(self):
        g1 = DataGenerator(seed=42)
        g2 = DataGenerator(seed=999)
        u1 = g1.generate_users(50)
        u2 = g2.generate_users(50)
        self.assertNotEqual(u1, u2)

    def test_same_seed_identical_orders(self):
        g1 = DataGenerator(seed=7)
        g2 = DataGenerator(seed=7)
        g1.generate_users(20)
        g2.generate_users(20)
        o1 = g1.generate_orders(50)
        o2 = g2.generate_orders(50)
        self.assertEqual(o1, o2)

    def test_same_seed_identical_trades(self):
        g1 = DataGenerator(seed=123)
        g2 = DataGenerator(seed=123)
        g1.generate_users(20)
        g2.generate_users(20)
        g1.generate_orders(30)
        g2.generate_orders(30)
        t1 = g1.generate_trades(40)
        t2 = g2.generate_trades(40)
        self.assertEqual(t1, t2)

    def test_reproducible_across_runs(self):
        results = []
        for _ in range(3):
            g = DataGenerator(seed=42)
            g.generate_users(10)
            results.append(g.users[0]["email"])
        self.assertEqual(len(set(results)), 1)

    def test_default_seed_is_set(self):
        g1 = DataGenerator()
        g2 = DataGenerator()
        self.assertEqual(g1.generate_users(5), g2.generate_users(5))


class TestHelpersHonourRng(unittest.TestCase):
    def test_random_phone_deterministic(self):
        rng1 = random.Random(1)
        rng2 = random.Random(1)
        self.assertEqual(random_phone(rng1), random_phone(rng2))

    def test_random_email_deterministic(self):
        rng1 = random.Random(2)
        rng2 = random.Random(2)
        self.assertEqual(random_email("john", "doe", rng1), random_email("john", "doe", rng2))

    def test_random_datetime_deterministic(self):
        rng1 = random.Random(3)
        rng2 = random.Random(3)
        self.assertEqual(random_datetime(rng=rng1), random_datetime(rng=rng2))

    def test_gaussian_random_deterministic(self):
        rng1 = random.Random(4)
        rng2 = random.Random(4)
        self.assertEqual(gaussian_random(0, 1, rng1), gaussian_random(0, 1, rng2))

    def test_helpers_default_to_global_when_no_rng(self):
        # without rng, helpers still work (fall back to global random)
        phone = random_phone()
        self.assertTrue(phone.startswith("+1-"))
        email = random_email("jane", "roe")
        self.assertIn("@", email)


if __name__ == "__main__":
    unittest.main()
