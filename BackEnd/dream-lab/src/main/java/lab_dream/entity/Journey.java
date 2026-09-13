package lab_dream.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;

/**
 * 访客足迹 —— 档案馆「访客之书」上的一行。
 *
 * 访客（也就是你）每向这个世界发出一条请求，
 * 拦截器就会往这张表里添一行：什么时候、做了什么。
 * 第 4 关你翻开档案馆时，看到的就是它。
 *
 * 顺便说：这其实就是后端工程师每天在写的「日志/审计」，
 * 只是这里我们把它存进了数据库，让它可以被翻阅。
 */
@Entity
@Table(name = "journey")
public class Journey {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY) // 让数据库自己分配自增编号
    private Long id;

    /** 请求方式：GET / POST …… */
    private String method;

    /** 请求的路径，比如 /memory/fragment/3 */
    private String path;

    /** 拼在路径后面的参数（第 3 关你试错的咒语都记在这） */
    private String query;

    /** 服务器回给访客的状态码：200 是「好嘞」，403 是「你谁啊」 */
    private Integer status;

    @Column(name = "created_at")
    private Instant createdAt;

    protected Journey() {
    }

    public Journey(String method, String path, String query, Integer status) {
        this.method = method;
        this.path = path;
        this.query = query;
        this.status = status;
        this.createdAt = Instant.now();
    }

    public Long getId() { return id; }
    public String getMethod() { return method; }
    public String getPath() { return path; }
    public String getQuery() { return query; }
    public Integer getStatus() { return status; }
    public Instant getCreatedAt() { return createdAt; }
}
