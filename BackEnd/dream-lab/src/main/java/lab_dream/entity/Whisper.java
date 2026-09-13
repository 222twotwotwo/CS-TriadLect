package lab_dream.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;

/**
 * 访客对 星野凛 说的悄悄话（第 7 关）。
 *
 * 这张表是整个世界里唯一一张「由玩家写入」的表：
 * 你敲下的那句话会被持久化——存在硬盘上，重启也不会消失。
 * 「持久化」这个词，你现在亲眼见到了。
 */
@Entity
@Table(name = "whisper")
public class Whisper {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 玩家想对 星野凛 说的话 */
    @Column(length = 200)
    private String message;

    @Column(name = "created_at")
    private Instant createdAt;

    protected Whisper() {
    }

    public Whisper(String message) {
        this.message = message;
        this.createdAt = Instant.now();
    }

    public Long getId() { return id; }
    public String getMessage() { return message; }
    public Instant getCreatedAt() { return createdAt; }
}
