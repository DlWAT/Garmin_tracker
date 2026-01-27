from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, false, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # We use Garmin email as the identity for the app.
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    # Display name chosen at signup.
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    # Stable internal id used by legacy JSON files and UI.
    user_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    pin_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Coaches can have athlete access; coaches can also be athletes.
    is_coach: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    garmin_account: Mapped[GarminAccount] = relationship(back_populates="user", uselist=False, cascade="all, delete")

    coach_athletes: Mapped[list[CoachAthlete]] = relationship(
        back_populates="coach",
        cascade="all, delete-orphan",
        foreign_keys="CoachAthlete.coach_user_id",
    )
    athlete_coaches: Mapped[list[CoachAthlete]] = relationship(
        back_populates="athlete",
        cascade="all, delete-orphan",
        foreign_keys="CoachAthlete.athlete_user_id",
    )


class CoachAthlete(Base):
    __tablename__ = "coach_athletes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    coach_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    athlete_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    coach: Mapped[User] = relationship(
        back_populates="coach_athletes",
        foreign_keys=[coach_user_id],
    )
    athlete: Mapped[User] = relationship(
        back_populates="athlete_coaches",
        foreign_keys=[athlete_user_id],
    )

    __table_args__ = (
        UniqueConstraint("coach_user_id", "athlete_user_id", name="uq_coach_athletes_pair"),
    )


class GarminAccount(Base):
    __tablename__ = "garmin_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # We do NOT store Garmin password (asked at sync time).
    garmin_email: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="garmin_account")

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_garmin_accounts_user_id"),
    )
