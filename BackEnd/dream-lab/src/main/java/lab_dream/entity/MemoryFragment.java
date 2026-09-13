package lab_dream.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;

/**
 * 记忆碎片。
 *
 * @Entity 的意思：这个类对应数据库里的一张表，一个对象 = 一行记录。
 * 表名 memory_fragment，它有 7 行，编号 1~7 —— 那就是散落世界各地的七片记忆。
 * 你不需要写一行 SQL，JPA 会替你把对象和表互相翻译。
 */
@Entity
@Table(name = "memory_fragment")
public class MemoryFragment {

    /** 主键：碎片的编号，1 到 7 */
    @Id
    private Integer id;

    /** 碎片的 title，比如「初次对话」「档案馆」 */
    private String title;

    /** 被玩家找回了没有。false = 还散落在外 */
    private boolean found;

    /** 什么时候被找回的（还没找回就是 null —— 时间也可以"不存在"） */
    private Instant foundAt;

    protected MemoryFragment() {
        // JPA 需要一个不带参数的构造方法（它通过反射创建对象，你以后会慢慢遇到这个词）
    }

    public MemoryFragment(Integer id, String title) {
        this.id = id;
        this.title = title;
        this.found = false;
    }

    /** 找到了！记录下这一刻。 */
    public void collect() {
        this.found = true;
        this.foundAt = Instant.now();
    }

    public Integer getId() { return id; }
    public String getTitle() { return title; }
    public boolean isFound() { return found; }
    public Instant getFoundAt() { return foundAt; }
}
