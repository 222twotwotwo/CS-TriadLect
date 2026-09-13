package lab_dream.mapper;

import lab_dream.entity.MemoryFragment;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

/**
 * 「仓库」：你声明想要什么，Spring Data JPA 帮你生成实现。
 *
 * 看到了吗？这个接口里没有任何实现代码——
 * findByFoundTrueOrderByIdAsc() 这个名字本身，就是一句 SQL：
 *   SELECT * FROM memory_fragment WHERE found = true ORDER BY id ASC
 * 方法名即查询，这就是 ORM 的魔法之一。
 */
public interface MemoryFragmentMapper extends JpaRepository<MemoryFragment, Integer> {

    /** 所有已找回的碎片，按找回顺序排列 */
    List<MemoryFragment> findByFoundTrueOrderByFoundAtAsc();

    /** 统计已找回几片 —— 名字里有 count，它就帮你数 */
    long countByFoundTrue();

    boolean existsByIdAndFoundTrue(Integer id);
}
